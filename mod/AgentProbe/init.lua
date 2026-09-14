--[[
    AgentProbe / init.lua  (v2, prudent)
    Phase 0 : sonde d'API + canal d'etat pour l'agent Cyberpunk.

    LECON DE LA v1 : le jeu plantait 1,2 s apres le debut de session. Les sondes
    tournaient dans onInit, avant que les systemes du jeu soient prets ; un appel
    natif mal forme ou trop precoce tue le processus, et pcall n'y peut rien.

    v2 :
      - rien ne tourne avant 5 s de jeu EFFECTIF (joueur attache)
      - une seule sonde par image, jamais plus
      - le nom de chaque sonde est ecrit ET flushe sur disque AVANT son execution
        (probe_progress.txt) : en cas de crash, la derniere ligne = la coupable
      - la boucle d'etat (state.bin) ne fait que position + cap, le strict
        necessaire au test d'entrees ; le reste sera ajoute une fois sonde

    state.bin : un seul fichier, handle garde ouvert, reecrit depuis le debut,
    JSON padde a largeur fixe, seq en debut ET fin (lecture dechiree rejetee).
    Pas d'os.rename (le sandbox CET refuse d'ecraser une destination existante).
]]

local STATE_WIDTH   = 5000     -- elargi : dialogue + ennemis + liste des hacks
local SAMPLE_PERIOD = 0.05      -- 20 Hz ; dt en SECONDES
local WARMUP        = 20.0      -- secondes de jeu avant la premiere sonde (5 s = encore en chargement)
local VITALS_PERIOD = 10.0      -- diagnostic repete : quand le monde devient-il exploitable ?
local vitalsAcc     = 0.0

local seq, acc, fh = 0, 0.0, nil
local lastPollErr = nil
-- declares ICI car onInit/onUpdate (definis plus haut que le canal) les referencent :
-- sinon Lua les resout comme des GLOBALES nil -> 'attempt to call a nil value'.
local dbReady = false
local pollCommands
local CMD_PERIOD = 0.25
local cmdAcc, lastCmdSeq = 0.0, -1      -- AVANT onInit (sinon globale nil -> derniere commande rejouee au demarrage)
local lastStateErr = nil                 -- derniere erreur du tick d etat (journalisee une fois)
local slowT, slowLoot, slowVehicles, slowNpcs = -99.0, nil, nil, nil   -- scans larges (TSQ_ALL) a 4 Hz, pas 20
local lastFtPoints = {}                  -- positions des bornes de voyage rapide (garde du teleport)
local lastVendorKey, lastVendorQty = nil, {}   -- marchand de vendor_stock (hash) et quantites en stock
local breachCtrl = nil                   -- HackingMinigameGameController capture a l ouverture du Breach Protocol
local bdClues, bdLastClueSig, bdFocusCache = {}, '', nil   -- danse sensorielle : indices de la timeline vus, entites-indices
local lastUpdateClock, lastDrawClock = 0.0, 0.0   -- onUpdate s arrete dans les menus (dont le mini-jeu) : onDraw prend le relais
local lastExport = nil                   -- derniere table d etat ecrite (reutilisee par onDraw pendant le mini-jeu)
local enemyDiag, lastEnemyDiag = '', ''
local aliveMemory, bodyMemory = {}, {}      -- derniere position des ennemis vus / corps (loot)
local lastInventory = {}                    -- ItemID par index de la derniere liste d inventaire
local qhListLogged = false                  -- structure de la liste des hacks journalisee une fois
local lastRecipes = {}                       -- TweakDBID par index de la derniere liste de recettes
local craftDiagDone = false


local lootClassesLogged = false             -- classes d objets lootables vues, journalisees une fois
local lookatLogged = false
local crimeAcc, lastCrimes = 0.0, nil
local lastDialogSig = ''
local attachedFor = 0.0         -- temps cumule avec un joueur attache
local probes, probeIdx = {}, 0
local probesDone = false

local function pad(s, n)
    if #s >= n then return s:sub(1, n) end
    return s .. string.rep(' ', n - #s)
end

-- Journal durable : ouvert, ecrit, flushe, ferme a chaque ligne. Survit a un crash.
local function journal(line)
    local f = io.open('probe_progress.txt', 'a')
    if f then f:write(line .. '\n'); f:flush(); f:close() end
end

local function addProbe(name, fn) probes[#probes + 1] = { name = name, fn = fn } end

-- NIVEAU RECOMMANDE d une quete : on remonte les parents de l entree de journal (objectif -> phase
-- -> quete) jusqu a une entree qui expose GetRecommendedLevelID / GetRecommendedLevel. Chaque appel
-- natif est journalise RUN/OK (un plantage designerait le coupable). Resultat mis en cache par hash.
local questLvlCache = {}
local function questLevelOf(jm, e, hash)
    if hash and questLvlCache[hash] ~= nil then return questLvlCache[hash] or nil end
    local lvl, raw = nil, nil
    local cur = e
    for depth = 1, 4 do
        if not cur then break end
        local cls = '?'
        pcall(function() cls = tostring(cur:GetClassName()) end)
        -- on ESSAIE les methodes (une methode absente leve une erreur Lua, attrapee par pcall ; pas de plantage natif)
        journal('RUN  questLevel d=' .. depth .. ' ' .. cls .. ' GetRecommendedLevel')
        local okL, v = pcall(function() return cur:GetRecommendedLevel() end)
        journal('OK   questLevel GetRecommendedLevel -> ' .. tostring(okL) .. '/' .. tostring(v))
        if okL and type(v) == 'number' and v > 0 then lvl = v; raw = tostring(v); break end
        journal('RUN  questLevel d=' .. depth .. ' ' .. cls .. ' GetRecommendedLevelID')
        local okI, id = pcall(function() return cur:GetRecommendedLevelID() end)
        local sid = nil
        if okI and id then
            pcall(function() sid = TDBID.ToStringDEBUG(id) end)
            if not sid then pcall(function() sid = tostring(id) end) end
        end
        journal('OK   questLevel GetRecommendedLevelID -> ' .. tostring(okI) .. '/' .. tostring(sid))
        if okI and sid then
            raw = sid
            -- l ID est un enregistrement TweakDB (ex. DeviceContentAssignment.ma_wat_kab_08) : on lit ses champs
            -- de niveau (noms possibles essayes un par un, journalises la premiere fois)
            for _, fl in ipairs({ 'powerLevelMin', 'powerLevelMax', 'powerLevel', 'recommendedLevel', 'level', 'contentLevel', 'minLevel', 'maxLevel', 'difficulty' }) do
                local okF, v = pcall(function() return TweakDB:GetFlat(sid .. '.' .. fl) end)
                if okF and v ~= nil then
                    journal('OK   questLevel flat ' .. fl .. ' = ' .. tostring(v))
                    if type(v) == 'number' and v > 0 and not lvl then lvl = math.floor(v) end
                end
            end
            if not lvl then
                local n = sid:match('_(%d+)$')
                if n then lvl = tonumber(n) end          -- dernier recours : le numero du palier dans le nom
            end
            if lvl then break end
        end
        local okP, parent = pcall(function() return jm:GetParentEntry(cur) end)
        cur = okP and parent or nil
    end
    journal('OK   questLevel final -> ' .. tostring(lvl) .. ' (' .. tostring(raw) .. ')')
    if hash then questLvlCache[hash] = lvl or false end
    return lvl, raw
end


-- ---- sondes, de la plus anodine a la plus risquee ------------------------------
addProbe('player:GetWorldPosition()', function()
    local p = Game.GetPlayer():GetWorldPosition(); return p.x, p.y, p.z
end)
addProbe('player:GetWorldYaw()', function()
    return Game.GetPlayer():GetWorldYaw()
end)
addProbe('player:IsAttached()', function()
    return Game.GetPlayer():IsAttached()
end)
addProbe('GetAllBlackboardDefs().PlayerStateMachine existe', function()
    return GetAllBlackboardDefs().PlayerStateMachine ~= nil
end)
addProbe('PSM GetLocalInstanced + IsMovingHorizontally', function()
    local defs = GetAllBlackboardDefs()
    local bb = Game.GetBlackboardSystem():GetLocalInstanced(Game.GetPlayer():GetEntityID(), defs.PlayerStateMachine)
    return bb ~= nil, bb and bb:GetBool(defs.PlayerStateMachine.IsMovingHorizontally)
end)
addProbe('PSM Combat (int) + gamePSMCombat.InCombat', function()
    local defs = GetAllBlackboardDefs()
    local bb = Game.GetBlackboardSystem():GetLocalInstanced(Game.GetPlayer():GetEntityID(), defs.PlayerStateMachine)
    return bb:GetInt(defs.PlayerStateMachine.Combat), gamePSMCombat.InCombat
end)
addProbe('StatPoolsSystem:GetStatPoolValue(Health, perc)', function()
    return Game.GetStatPoolsSystem():GetStatPoolValue(Game.GetPlayer():GetEntityID(), gamedataStatPoolType.Health, true)
end)
addProbe('CameraSystem:GetActiveCameraForward()', function()
    local f = Game.GetCameraSystem():GetActiveCameraForward(); return f.x, f.y, f.z
end)
addProbe('CameraSystem:ProjectPoint(pos joueur)', function()
    local s = Game.GetCameraSystem():ProjectPoint(Game.GetPlayer():GetWorldPosition()); return s.x, s.y
end)
addProbe('MappinSystem existe', function() return Game.GetMappinSystem() ~= nil end)
addProbe('JournalManager:GetTrackedEntry()', function()
    return Game.GetJournalManager():GetTrackedEntry() ~= nil
end)
addProbe('os.rename ecrase une destination existante ? (attendu: nil)', function()
    local a = io.open('probe_a.txt', 'w'); a:write('a'); a:close()
    local b = io.open('probe_b.txt', 'w'); b:write('b'); b:close()
    local ok, err = os.rename('probe_a.txt', 'probe_b.txt')
    os.remove('probe_a.txt'); os.remove('probe_b.txt')
    return ok, err
end)
addProbe('NavigationSystem:CalculatePathOnlyHumanNavmesh (pos->pos)', function()
    local p = Game.GetPlayer():GetWorldPosition()
    local path = Game.GetNavigationSystem():CalculatePathOnlyHumanNavmesh(p, p, NavGenAgentSize.Human, 2.0)
    return path ~= nil
end)
addProbe('TargetingSystem:GetTargetParts(TSQ_NPC)', function()
    local q = Game['TSQ_NPC;']()
    q.maxDistance = 40.0
    local ok, parts = Game.GetTargetingSystem():GetTargetParts(Game.GetPlayer(), q)
    return ok, parts and #parts or -1
end)
-- ---- v3 : les briques des competences (dialogue, quete, chemin, menaces) -------
addProbe('UIInteractions blackboard + DialogChoiceHubs (FromVariant)', function()
    local defs = GetAllBlackboardDefs()
    local bb = Game.GetBlackboardSystem():Get(defs.UIInteractions)
    local hubs = FromVariant(bb:GetVariant(defs.UIInteractions.DialogChoiceHubs))
    local n = (hubs and hubs.choiceHubs) and #hubs.choiceHubs or 0
    return bb ~= nil, n, bb:GetInt(defs.UIInteractions.SelectedIndex)
end)
addProbe('Journal: GetTrackedEntry -> GetEntryHash -> description localisee', function()
    local jm = Game.GetJournalManager()
    local e = jm:GetTrackedEntry()
    if not e then return 'aucune quete suivie' end
    local h = jm:GetEntryHash(e)
    local d = e.GetDescription and e:GetDescription() or '?'
    return h, tostring(d):sub(1, 60), GetLocalizedText(tostring(d)):sub(1, 60)
end)
addProbe('MappinSystem:GetQuestMappinPositionsByObjective(hash cast Uint32)', function()
    local jm = Game.GetJournalManager()
    local e = jm:GetTrackedEntry()
    if not e then return 'aucune quete suivie' end
    local h = jm:GetEntryHash(e)
    if h < 0 then h = h + 4294967296 end
    local ok, positions = Game.GetMappinSystem():GetQuestMappinPositionsByObjective(h)
    local n = positions and #positions or 0
    local first = (n > 0) and string.format('%.1f %.1f %.1f', positions[1].x, positions[1].y, positions[1].z) or '-'
    return ok, n, first
end)
-- v4 : coller les extremites au navmesh AVANT de demander un chemin
addProbe('Nav: FindPointInSphereOnlyHumanNavmesh(pos joueur, r=3)', function()
    local pos = Game.GetPlayer():GetWorldPosition()
    local r = Game.GetNavigationSystem():FindPointInSphereOnlyHumanNavmesh(pos, 3.0, NavGenAgentSize.Human, false)
    if not r then return 'nil' end
    return tostring(r.status), r.point and string.format('%.1f %.1f %.1f', r.point.x, r.point.y, r.point.z) or '-'
end)
addProbe('Nav: chemin entre 2 points COLLES (joueur -> 12 m devant)', function()
    local p = Game.GetPlayer()
    local pos, fwd = p:GetWorldPosition(), p:GetWorldForward()
    local ns = Game.GetNavigationSystem()
    local a = ns:FindPointInSphereOnlyHumanNavmesh(pos, 3.0, NavGenAgentSize.Human, false)
    local rawB = Vector4.new(pos.x + fwd.x * 12, pos.y + fwd.y * 12, pos.z, 1.0)
    local b = ns:FindPointInSphereOnlyHumanNavmesh(rawB, 6.0, NavGenAgentSize.Human, false)
    if not (a and a.point and b and b.point) then return 'snap echoue', tostring(a and a.status), tostring(b and b.status) end
    local path = ns:CalculatePathOnlyHumanNavmesh(a.point, b.point, NavGenAgentSize.Human, 2.0)
    if not path then return 'nil (pas de chemin malgre snap)' end
    local n = #path.path
    local last = path.path[n]
    return n, path:CalculateLength(), string.format('fin %.1f %.1f %.1f', last.x, last.y, last.z)
end)
addProbe('Nav: IsNavmeshStreamedInLocation(pos, 1.0)', function()
    return Game.GetNavigationSystem():IsNavmeshStreamedInLocation(Game.GetPlayer():GetWorldPosition(), 1.0)
end)
-- v5 : l enum d agent est-il valide ? (nil silencieux = requetes invalides)
addProbe('Enum: NavGenAgentSize.Human / EnumInt / Dump', function()
    local h = NavGenAgentSize.Human
    local i = nil; pcall(function() i = EnumInt(h) end)
    local names = {}
    pcall(function() for k, _ in pairs(NavGenAgentSize) do names[#names + 1] = tostring(k) end end)
    return tostring(h), tostring(i), table.concat(names, ','):sub(1, 120)
end)
-- v5 : seconde API de pathfinding, avec le joueur comme agent
addProbe('AINav: IsPointOnNavmesh(player, pos, 1.0)', function()
    local p = Game.GetPlayer()
    return Game.GetAINavigationSystem():IsPointOnNavmesh(p, p:GetWorldPosition(), 1.0)
end)
addProbe('AINav: FindPointInSphereForCharacter(pos, 3, player)', function()
    local p = Game.GetPlayer()
    local r = Game.GetAINavigationSystem():FindPointInSphereForCharacter(p:GetWorldPosition(), 3.0, p)
    if not r then return 'nil' end
    return tostring(r.status), r.point and string.format('%.1f %.1f %.1f', r.point.x, r.point.y, r.point.z) or '-'
end)
addProbe('AINav: CalculatePathForCharacter(pos -> 12 m devant, tol 2, player)', function()
    local p = Game.GetPlayer()
    local pos, fwd = p:GetWorldPosition(), p:GetWorldForward()
    local dst = Vector4.new(pos.x + fwd.x * 12, pos.y + fwd.y * 12, pos.z, 1.0)
    local path = Game.GetAINavigationSystem():CalculatePathForCharacter(pos, dst, 2.0, p)
    if not path then return 'nil (pas de chemin)' end
    local n = #path.path
    local last = path.path[n]
    return n, path:CalculateLength(), string.format('fin %.1f %.1f %.1f', last.x, last.y, last.z)
end)
-- v4 : ou est le marqueur de quete quand l objectif vise un PNJ ?
addProbe('Journal: GetDistanceToNearestMappin(objectif suivi)', function()
    local jm = Game.GetJournalManager()
    local e = jm:GetTrackedEntry()
    if not e then return 'aucune quete suivie' end
    return jm:GetDistanceToNearestMappin(e)
end)
addProbe('Mappin: GetMappinFromObjective(parent, objectif) -> position', function()
    local jm = Game.GetJournalManager()
    local e = jm:GetTrackedEntry()
    if not e then return 'aucune quete suivie' end
    local parent = jm:GetParentEntry(e)
    local m = Game.GetMappinSystem():GetMappinFromObjective(parent, e)
    if not m then return 'nil' end
    local pos = m:GetWorldPosition()
    return string.format('%.1f %.1f %.1f', pos.x, pos.y, pos.z), m.IsQuestMappin and tostring(m:IsQuestMappin()) or '?'
end)
-- v5 : ces deux fonctions n ont PAS de valeur de retour -> le parametre out
-- revient SEUL, en premiere position (lecon v4 : je lisais un booleen inexistant)
addProbe('Mappin: GetMappinEntries(Minimap) -> nb + 5 premiers', function()
    local entries = Game.GetMappinSystem():GetMappinEntries(gamemappinsMappinTargetType.Minimap)
    if type(entries) ~= 'table' then return 'type ' .. type(entries) end
    local out = {}
    for i = 1, math.min(5, #entries) do
        local w = entries[i].worldPosition
        out[#out + 1] = string.format('[%.0f,%.0f,%.0f]', w.x, w.y, w.z)
    end
    return #entries, table.concat(out, ' ')
end)
addProbe('Mappin: GetMappins(Minimap) -> nb, quete (variant@pos)', function()
    local mappins = Game.GetMappinSystem():GetMappins(gamemappinsMappinTargetType.Minimap)
    if type(mappins) ~= 'table' then return 'type ' .. type(mappins) end
    local quest, kinds = {}, {}
    for i = 1, #mappins do
        local m = mappins[i]
        local v = '?'; pcall(function() v = tostring(m:GetVariant()) end)
        kinds[v] = (kinds[v] or 0) + 1
        local isQ = false; pcall(function() isQ = m:IsQuestMappin() end)
        if isQ then
            local w = m:GetWorldPosition()
            quest[#quest + 1] = string.format('%s@[%.0f,%.0f,%.0f]', v, w.x, w.y, w.z)
        end
    end
    local ks = {}
    for k, n in pairs(kinds) do ks[#ks + 1] = k .. 'x' .. n end
    return #mappins, table.concat(quest, ' '):sub(1, 140), table.concat(ks, ' '):sub(1, 140)
end)
addProbe('TargetTrackerComponent:GetHostileThreats(false)', function()
    local t = Game.GetPlayer():GetTargetTrackerComponent():GetHostileThreats(false)
    return t and #t or -1
end)
addProbe('TargetingSystem:IsAnyEnemyVisible(player, 60)', function()
    return Game.GetTargetingSystem():IsAnyEnemyVisible(Game.GetPlayer(), 60.0)
end)
addProbe('GetDisplayResolution()', function() return GetDisplayResolution() end)
addProbe('QH: blackboards UI_QuickSlots / UI_Scanner / UI_ComDevice existent ?', function()
    local defs = GetAllBlackboardDefs()
    return defs.UI_QuickSlots ~= nil, defs.UI_Scanner ~= nil, defs.UI_ComDevice ~= nil
end)
addProbe('QH: champs candidats de UI_QuickSlots', function()
    local defs = GetAllBlackboardDefs()
    local d = defs.UI_QuickSlots
    if not d then return 'absent' end
    local found = {}
    for _, k in ipairs({ 'quickhackPanelOpen', 'quickHackPanelOpen', 'quickhackListSelectedIndex',
                         'QuickhackListSelectedIndex', 'quickhackSelectedIndex', 'quickHackDataSelectedIndex',
                         'quickhacksListData', 'quickHackListData', 'ScannerQuickHacks', 'quickSlotsData' }) do
        local ok, v = pcall(function() return d[k] end)
        if ok and v ~= nil then found[#found + 1] = k end
    end
    return table.concat(found, ',')
end)
addProbe('QH: champs candidats de UI_Scanner', function()
    local defs = GetAllBlackboardDefs()
    local d = defs.UI_Scanner
    if not d then return 'absent' end
    local found = {}
    for _, k in ipairs({ 'ScannerObjectId', 'ScannedObjectId', 'ScannerQuickHacks', 'QuickHackPanelOpen',
                         'ScannerData', 'ScannerAttitude', 'ScannerHealth', 'currentScannerState' }) do
        local ok, v = pcall(function() return d[k] end)
        if ok and v ~= nil then found[#found + 1] = k end
    end
    return table.concat(found, ',')
end)
addProbe('QH: PlayerStateMachine.CyberwareAbility / Sandevistan (int)', function()
    local defs = GetAllBlackboardDefs()
    local bb = Game.GetBlackboardSystem():GetLocalInstanced(Game.GetPlayer():GetEntityID(), defs.PlayerStateMachine)
    local a, b = 'n/a', 'n/a'
    pcall(function() a = bb:GetInt(defs.PlayerStateMachine.Sandevistan) end)
    pcall(function() b = bb:GetInt(defs.PlayerStateMachine.Berserk) end)
    return tostring(a), tostring(b)
end)
addProbe('DUMP: GameDump(UI_Scanner def) -> champs', function()
    local d = GetAllBlackboardDefs().UI_Scanner
    return d and GameDump(d):gsub('%s+', ' '):sub(1, 700) or 'absent'
end)
addProbe('DUMP: GameDump(UIInteractions def) -> champs', function()
    local d = GetAllBlackboardDefs().UIInteractions
    return d and GameDump(d):gsub('%s+', ' '):sub(1, 700) or 'absent'
end)
addProbe('DUMP: noms de defs contenant Quick/Hack/Scanner', function()
    local all = GetAllBlackboardDefs()
    local s = GameDump(all):gsub('%s+', ' ')
    local found = {}
    for name in s:gmatch('([%w_]*[Qq]uick[%w_]*)') do found[name] = true end
    for name in s:gmatch('([%w_]*[Hh]ack[%w_]*)') do found[name] = true end
    for name in s:gmatch('([%w_]*[Ss]canner[%w_]*)') do found[name] = true end
    local out = {}
    for k in pairs(found) do out[#out + 1] = k end
    table.sort(out)
    return table.concat(out, ','):sub(1, 600)
end)
addProbe('ARMES: slots WeaponWheelSlot1..3 -> nom + DPS', function()
    local ts = Game.GetTransactionSystem()
    local p = Game.GetPlayer()
    local out = {}
    for i = 1, 3 do
        local slot = TweakDBID.new('AttachmentSlots.WeaponWheelSlot' .. i)
        local item = ts:GetItemInSlot(p, slot)
        if item then
            local data = item:GetItemData()
            local dps = data and data:GetStatValueByType(gamedataStatType.EffectiveDPS) or -1
            local dmg = data and data:GetStatValueByType(gamedataStatType.DPS) or -1
            local name = '?'
            pcall(function() name = GetLocalizedTextByKey(TweakDBInterface.GetItemRecord(item:GetItemID().id):DisplayName()) end)
            out[#out + 1] = string.format('%d:%s dps=%.0f|%.0f', i, name, dps, dmg)
        else
            out[#out + 1] = i .. ':vide'
        end
    end
    return table.concat(out, ' ; ')
end)
addProbe('ARME ACTIVE: GetActiveWeapon -> nom, type', function()
    local p = Game.GetPlayer()
    local w = p:GetActiveWeapon()
    if not w then return 'aucune' end
    local name = '?'
    pcall(function() name = GetLocalizedTextByKey(TweakDBInterface.GetItemRecord(w:GetItemID().id):DisplayName()) end)
    local t = '?'
    pcall(function() t = tostring(w:GetWeaponRecord():ItemType():Type()) end)
    return name, t
end)
addProbe('DUMP: UI_QuickSlotsData def', function()
    local d = GetAllBlackboardDefs().UI_QuickSlotsData
    return d and GameDump(d):gsub('%s+', ' '):gsub('gamebbScriptID_', ''):gsub('%[ None:gamebbID%[ g:[%w_]+ %] %]', ''):sub(1, 900) or 'absent'
end)
addProbe('DUMP: PlayerQuickHackData def', function()
    local d = GetAllBlackboardDefs().PlayerQuickHackData
    return d and GameDump(d):gsub('%s+', ' '):gsub('gamebbScriptID_', ''):gsub('%[ None:gamebbID%[ g:[%w_]+ %] %]', ''):sub(1, 900) or 'absent'
end)
addProbe('DUMP: UI_Hacking + UI_ScannerModules defs', function()
    local a = GetAllBlackboardDefs().UI_Hacking
    local b = GetAllBlackboardDefs().UI_ScannerModules
    local f = function(d) return d and GameDump(d):gsub('%s+', ' '):gsub('gamebbScriptID_', ''):gsub('%[ None:gamebbID%[ g:[%w_]+ %] %]', ''):sub(1, 450) or 'absent' end
    return f(a), f(b)
end)
addProbe('EQUIP: EquipmentSystem Weapon slots 0..2 -> nom, type, DPS', function()
    local p = Game.GetPlayer()
    local es = Game.GetScriptableSystemsContainer():Get('EquipmentSystem')
    local data = es:GetPlayerData(p)
    local ts = Game.GetTransactionSystem()
    local out = {}
    for i = 0, 2 do
        local id = data:GetItemInEquipSlot(gamedataEquipmentArea.Weapon, i)
        if id and ItemID.IsValid(id) then
            local name, typ, dps = '?', '?', -1
            pcall(function() name = GetLocalizedTextByKey(TweakDBInterface.GetItemRecord(id.id):DisplayName()) end)
            pcall(function() typ = tostring(TweakDBInterface.GetItemRecord(id.id):ItemType():Type()) end)
            pcall(function() dps = ts:GetItemData(p, id):GetStatValueByType(gamedataStatType.EffectiveDPS) end)
            out[#out + 1] = string.format('%d:%s (%s) dps=%.0f', i + 1, name, typ, dps)
        else
            out[#out + 1] = (i + 1) .. ':vide'
        end
    end
    return table.concat(out, ' ; ')
end)
-- sondes CRAFT / VENTE (2026-09-11) : existence des systemes et structure du livre de recettes
addProbe('CRAFT: CraftingSystem + craftbook (GameDump)', function()
    local cs = Game.GetScriptableSystemsContainer():Get('CraftingSystem')
    if not cs then return 'CraftingSystem absent' end
    local cb = cs:GetPlayerCraftBook()
    if not cb then return 'craftbook absent' end
    return GameDump(cb):gsub('%s+', ' '):sub(1, 900)
end)
addProbe('CRAFT: recettes connues (m_knownRecipes) -> nb + 6 premieres', function()
    local cs = Game.GetScriptableSystemsContainer():Get('CraftingSystem')
    local cb = cs:GetPlayerCraftBook()
    local arr = nil
    pcall(function() arr = cb.m_knownRecipes end)
    if type(arr) ~= 'table' then
        local ok2, r2 = pcall(function() return cb:GetKnownRecipes() end)
        if ok2 and type(r2) == 'table' then arr = r2 end
    end
    if type(arr) ~= 'table' then return 'aucun acces aux recettes' end
    local names = {}
    for i = 1, math.min(#arr, 6) do
        local rec = arr[i]
        local n = '?'
        pcall(function() n = GetLocalizedTextByKey(TweakDBInterface.GetItemRecord(rec.targetItem):DisplayName()) end)
        pcall(function() if n == '?' then n = tostring(rec.targetItem) end end)
        names[#names + 1] = n
    end
    return #arr, table.concat(names, ' | ')
end)
addProbe('CRAFT: fonctions CraftItem / CanItemBeCrafted presentes ?', function()
    local cs = Game.GetScriptableSystemsContainer():Get('CraftingSystem')
    return tostring(cs.CraftItem ~= nil), tostring(cs.CanItemBeCrafted ~= nil), tostring(cs.GetItemCraftingRecipe ~= nil)
end)
addProbe('VENTE: fonctions de marquage camelote (ItemActionsHelper.*Junk*, TransactionSystem.*Junk*)', function()
    local found = {}
    for _, n in ipairs({ 'MarkItemAsJunk', 'MarkAsJunk', 'SetItemJunk', 'ToggleJunk', 'MarkItemAsFavorite', 'SellItem', 'DropItem', 'UseItem', 'EatItem' }) do
        pcall(function() if ItemActionsHelper[n] ~= nil then found[#found + 1] = 'IAH.' .. n end end)
    end
    local ts = Game.GetTransactionSystem()
    for _, n in ipairs({ 'MarkItemAsJunk', 'SetJunk', 'SellItem', 'TransferItem', 'GiveItem', 'RemoveItem', 'GetItemList' }) do
        pcall(function() if ts[n] ~= nil then found[#found + 1] = 'TS.' .. n end end)
    end
    return table.concat(found, ',')
end)
addProbe('VENTE: fonctions d etiquette (TransactionSystem.AddItemTag/HasItemTag, gameItemData.HasTag)', function()
    local ts = Game.GetTransactionSystem()
    local found = {}
    for _, n in ipairs({ 'AddItemTag', 'RemoveItemTag', 'HasItemTag', 'HasTag', 'MarkItemAsJunk', 'SetItemTag', 'AddTag' }) do
        pcall(function() if ts[n] ~= nil then found[#found + 1] = 'TS.' .. n end end)
    end
    for _, n in ipairs({ 'MarkItemAsJunk', 'ToggleJunk', 'AddItemTag', 'MarkAsJunk', 'SetJunk', 'ToggleItemJunkTag' }) do
        pcall(function() if ItemActionsHelper[n] ~= nil then found[#found + 1] = 'IAH.' .. n end end)
        pcall(function() if RPGManager[n] ~= nil then found[#found + 1] = 'RPG.' .. n end end)
    end
    return table.concat(found, ',')
end)
addProbe('VENTE: MarketSystem / RPGManager.CalculateSellPrice / vendeurs sur minimap', function()
    local ms = Game.GetScriptableSystemsContainer():Get('MarketSystem')
    local hasSell = false
    pcall(function() hasSell = (RPGManager.CalculateSellPrice ~= nil) end)
    local vendors = {}
    pcall(function()
        local mappins = Game.GetMappinSystem():GetMappins(gamemappinsMappinTargetType.Minimap)
        local pos = Game.GetPlayer():GetWorldPosition()
        for i = 1, #mappins do
            local v = ''
            pcall(function() v = tostring(mappins[i]:GetVariant()):gsub('gamedataMappinVariant : ', ''):gsub(' %(%d+%)', '') end)
            if v:lower():find('vendor') or v:lower():find('shop') or v:lower():find('ripper') or v:lower():find('junk') then
                local w = mappins[i]:GetWorldPosition()
                local dd = math.sqrt((w.x - pos.x) ^ 2 + (w.y - pos.y) ^ 2)
                vendors[#vendors + 1] = string.format('%s@%.0fm', v, dd)
            end
        end
        table.sort(vendors)
    end)
    return tostring(ms ~= nil), tostring(hasSell), table.concat(vendors, ' '):sub(1, 400)
end)
addProbe('VOYAGE RAPIDE: FastTravelSystem + fonctions (existence seulement)', function()
    local fts = Game.GetFastTravelSystem()
    if not fts then return 'absent' end
    local found = {}
    for _, n in ipairs({ 'GetFastTravelPoints', 'PerformFastTravel', 'IsFastTravelEnabled', 'GetFastTravelPointsCount', 'FastTravelToPoint', 'RegisterFastTravelPoint' }) do
        pcall(function() if fts[n] ~= nil then found[#found + 1] = n end end)
    end
    local n = -1
    pcall(function() local pts = fts:GetFastTravelPoints(); n = (type(pts) == 'table') and #pts or -2 end)
    return table.concat(found, ','), n
end)
addProbe('NIVEAU: PlayerDevelopmentSystem points attribut/perk + niveau', function()
    local pds = Game.GetScriptableSystemsContainer():Get('PlayerDevelopmentSystem')
    local pdd = pds:GetData(Game.GetPlayer())
    local a, p, lvl = -1, -1, -1
    pcall(function() a = pdd:GetDevPoints(gamedataDevelopmentPointType.Attribute) end)
    pcall(function() p = pdd:GetDevPoints(gamedataDevelopmentPointType.Primary) end)
    pcall(function() lvl = Game.GetStatsSystem():GetStatValue(Game.GetPlayer():GetEntityID(), gamedataStatType.Level) end)
    return a, p, lvl
end)
-- sondes BREACH PROTOCOL (piratage des terminaux) : structure des blackboards du mini-jeu
addProbe('SMS: methodes JournalManager (contacts, messages, choix)', function()
    local jm = Game.GetJournalManager()
    local found = {}
    for _, n in ipairs({ 'GetContactList', 'GetContacts', 'GetMessages', 'GetPhoneMessages', 'GetFlattenedPhoneChoices', 'GetActivePhoneChoices',
                         'GetConversations', 'GetContactDataArray', 'GetMessagesAndChoices', 'GetUnreadMessages', 'GetRecentMessages', 'GetTrackedEntry',
                         'GetEntryState', 'ChangeEntryState', 'GetQuestObjectives', 'GetPhoneChoices', 'GetChildren' }) do
        local ok, v = pcall(function() return jm[n] end)
        if ok and v ~= nil then found[#found + 1] = n end
    end
    local okC, contacts = pcall(function() return jm:GetContactList() end)
    local nC = (okC and type(contacts) == 'table') and #contacts or -1
    return table.concat(found, ','), 'contacts=' .. tostring(nC) .. (okC and '' or (' err=' .. tostring(contacts)))
end)
addProbe('TELEPHONE: GameDump(UI_ComDevice def)', function()
    local d = GetAllBlackboardDefs().UI_ComDevice
    return d and GameDump(d):gsub('%s+', ' '):gsub('gamebbScriptID_', ''):gsub('%[ None:gamebbID%[ g:[%w_]+ %] %]', ''):sub(1, 900) or 'absent'
end)
addProbe('BREACH: GameDump(HackingMinigame def)', function()
    local d = GetAllBlackboardDefs().HackingMinigame
    return d and GameDump(d):gsub('%s+', ' '):gsub('gamebbScriptID_', ''):gsub('%[ None:gamebbID%[ g:[%w_]+ %] %]', ''):sub(1, 900) or 'absent'
end)
addProbe('BREACH: GameDump(HackingData def)', function()
    local d = GetAllBlackboardDefs().HackingData
    return d and GameDump(d):gsub('%s+', ' '):gsub('gamebbScriptID_', ''):gsub('%[ None:gamebbID%[ g:[%w_]+ %] %]', ''):sub(1, 600) or 'absent'
end)
addProbe('Argent : TransactionSystem:GetItemQuantity(player, MarketSystem.Money())', function()
    return Game.GetTransactionSystem():GetItemQuantity(Game.GetPlayer(), MarketSystem.Money())
end)

-- RETIRE : TimeSystem:SetTimeDilation('...', 1.0, 0.05) fait PLANTER le jeu
-- (crash natif, confirme par probe_progress.txt le 2026-09-10 15:05). Ne pas
-- reintroduire sans passer par un shim redscript.

local function runNextProbe()
    probeIdx = probeIdx + 1
    local pr = probes[probeIdx]
    if not pr then
        probesDone = true
        journal('===== toutes les sondes terminees =====')
        print('[AgentProbe] sondes terminees, voir probe_progress.txt')
        return
    end
    journal('RUN  ' .. pr.name)                 -- ecrit AVANT : derniere ligne = coupable si crash
    local ok, a, b, c = pcall(pr.fn)
    journal(string.format('%s %s -> %s %s %s', ok and 'OK  ' or 'FAIL', pr.name,
        tostring(a), tostring(b), tostring(c)))
end

-- ---- DANSE SENSORIELLE (braindance) --------------------------------------------------------------------------
local function enumNum(v)
    local okE, n = pcall(function() return EnumInt(v) end)
    if okE and type(n) == 'number' then return n end
    local d = tostring(v):match('%((%d+)%)')
    if d then return tonumber(d) end
    return tonumber(v)
end
local function bdSceneIface()
    local si = nil
    pcall(function() si = Game.GetSceneSystem():GetScriptInterface() end)
    return si
end
local function bdEnum(kind, name, idx)
    local v = nil
    pcall(function() v = _G[kind][name] end)
    if v == nil then pcall(function() v = Enum.new(kind, name) end) end
    if v == nil then v = idx end
    return v
end
local function bdState()
    local bd = nil
    pcall(function()
        local def = GetAllBlackboardDefs().Braindance
        local bb = def and Game.GetBlackboardSystem():Get(def) or nil
        if not bb then return end
        local active = false
        pcall(function() active = bb:GetBool(def.IsActive) end)
        local si = bdSceneIface()
        local rew = false
        if si then pcall(function() rew = si:IsRewindableSectionActive() end) end
        if not active and not rew then
            if next(bdClues) ~= nil then bdClues, bdLastClueSig, bdFocusCache = {}, '', nil end
            return
        end
        bd = { active = active, rew = rew }
        pcall(function() bd.fpp = bb:GetBool(def.IsFPP) end)
        pcall(function() bd.layer = bb:GetInt(def.activeBraindanceVisionMode) end)
        pcall(function() bd.exit = bb:GetBool(def.EnableExit) end)
        pcall(function() bd.prog = bb:GetFloat(def.Progress) end)
        if si then
            pcall(function() bd.t = si:GetRewindableSectionTimeInSec() end)
            pcall(function() bd.dur = si:GetRewindableSectionDurationInSec() end)
            pcall(function() bd.paused = si:IsRewindableSectionPaused() end)
            pcall(function() bd.speed = enumNum(si:GetRewindableSectionPlaySpeed()) end)
            pcall(function() bd.dir = enumNum(si:GetRewindableSectionPlayDirection()) end)
        end
        -- indice de la timeline (dernier evenement du blackboard) : accumule par nom
        pcall(function()
            local c = FromVariant(bb:GetVariant(def.Clue))
            if c and c.clueName then
                local name = tostring(c.clueName)
                local mode = tostring(c.mode)
                local sig = name .. '|' .. mode .. '|' .. tostring(c.startTime)
                if sig ~= bdLastClueSig and name ~= '' and name ~= 'None' then
                    bdLastClueSig = sig
                    local rec = bdClues[name] or { name = name }
                    rec.t0, rec.t1 = tonumber(c.startTime), tonumber(c.endTime)
                    rec.layer = enumNum(c.layer)
                    rec.mode = mode
                    if mode:find('Finish') or mode:find('%(2%)') then rec.done = true end
                    bdClues[name] = rec
                    journal(string.format('BD indice %s mode=%s t=%.1f-%.1f couche=%s', name, mode, rec.t0 or -1, rec.t1 or -1, tostring(rec.layer)))
                end
            end
        end)
        local list = {}
        for _, rec in pairs(bdClues) do list[#list + 1] = rec end
        table.sort(list, function(a, b) return (a.t0 or 0) < (b.t0 or 0) end)
        bd.clues = list
        pcall(function()
            local sys = Game.GetScriptableSystemsContainer():Get('BraindanceSystem')
            local m = sys:GetInputMask()
            bd.masks = { pause = m.pauseAction, fwd = m.playForwardAction, back = m.playBackwardAction, restart = m.restartAction,
                         layer = m.switchLayerAction, cam = m.cameraToggleAction }
            bd.inbd = sys:GetIsInBraindance()
        end)
    end)
    return bd
end
-- entites porteuses d un indice de danse sensorielle (ScanningComponent.IsBraindanceClue) a < 40 m
local function bdScanFocus(player, pos)
    local list, seen = {}, {}
    local q = Game['TSQ_ALL;']()
    q.maxDistance = 40.0
    q.filterObjectByDistance = true
    pcall(function() q.testedSet = TargetingSet.Complete end)
    local okF, partsF = Game.GetTargetingSystem():GetTargetParts(player, q)
    if not (okF and partsF) then return list end
    local cam = Game.GetCameraSystem()
    for i = 1, #partsF do
        local comp = TS_TargetPartInfo.GetComponent(partsF[i])
        local ent = comp and comp:GetEntity() or nil
        if ent then
            local okH, h = pcall(function() return ent:GetEntityID().hash end)
            local key = okH and tostring(h) or tostring(ent)
            if not seen[key] then
                seen[key] = true
                local sc = nil
                pcall(function() sc = ent:FindComponentByName('scanning') end)
                local isClue = false
                if sc then pcall(function() isClue = sc:IsBraindanceClue() end) end
                if isClue then
                    local ep = ent:GetWorldPosition()
                    local rec = { id = key, x = ep.x, y = ep.y, z = ep.z }
                    pcall(function() local sp = sc:GetBoundingSphere(); if sp and sp.centre then rec.x, rec.y, rec.z, rec.r = sp.centre.x, sp.centre.y, sp.centre.z, sp.radius end end)
                    rec.d = math.sqrt((rec.x - pos.x) ^ 2 + (rec.y - pos.y) ^ 2 + (rec.z - pos.z) ^ 2)
                    pcall(function() rec.layer = enumNum(sc:GetBraindanceLayer()) end)
                    pcall(function() rec.scanned = sc:IsScanned() end)
                    pcall(function() rec.blocked = sc:IsBraindanceBlocked() end)
                    pcall(function() rec.prog = sc:GetScanningProgress() end)
                    pcall(function() rec.need = sc:GetTimeNeeded() end)
                    pcall(function() rec.enabled = sc:IsAnyClueEnabled() end)
                    pcall(function() rec.insp = sc:IsClueInspected() end)
                    pcall(function() rec.scanning = sc:IsScanning() end)
                    pcall(function() rec.name = GetLocalizedText(tostring(ent:GetDisplayName())) end)
                    pcall(function() rec.cls = tostring(ent:GetClassName()) end)
                    pcall(function() local s2 = cam:ProjectPoint(Vector4.new(rec.x, rec.y, rec.z, 1.0)); rec.sx, rec.sy = s2.x, s2.y end)
                    list[#list + 1] = rec
                end
            end
        end
    end
    table.sort(list, function(a, b) return a.d < b.d end)
    return list
end

-- ---- BREACH PROTOCOL --------------------------------------------------------------------------------------
local function breachState()
    local st, tm = 0, nil
    pcall(function()
        local def = GetAllBlackboardDefs().HackingMinigame
        local bb = def and Game.GetBlackboardSystem():Get(def) or nil
        if bb then st = bb:GetInt(def.State) or 0; pcall(function() tm = bb:GetFloat(def.TimerLeftPercent) end) end
    end)
    return st, tm
end
local function breachLastPos()
    local last = nil
    pcall(function()
        local def = GetAllBlackboardDefs().HackingMinigame
        local bb = Game.GetBlackboardSystem():Get(def)
        local v = bb:GetVector4(def.LastPlayerHackPosition)
        if v then last = { x = v.x, y = v.y } end
    end)
    return last
end
-- parcours des widgets ink du mini-jeu : tous les textes hexadecimaux (1C, 55, BD, E9, 7A, FF) avec leur position
-- ABSOLUE (somme des positions dans les parents) et la geometrie de leur parent (la case cliquable)
local function breachInfo(cmd)
    local info = { ok = false }
    info.state, info.timer = breachState()
    info.last = breachLastPos()
    pcall(function()
        local def = GetAllBlackboardDefs().HackingMinigame
        local bb = Game.GetBlackboardSystem():Get(def)
        local md = FromVariant(bb:GetVariant(def.MinigameDefaults))
        if md then info.size = tonumber(md.gridSize); info.buffer = tonumber(md.bufferSize); info.timeLimit = tonumber(md.timeLimit) end
    end)
    if not breachCtrl then info.reason = 'controleur non capture (mini-jeu ferme ou hook absent)'; return info end
    -- chaines (sequences numeriques) + programmes (noms)
    pcall(function()
        local chains = breachCtrl:GetProgramsChains()
        info.chains = {}
        for i = 1, #chains do
            local c = chains[i]
            local r = {}
            pcall(function() for j = 1, #c.rarities do r[#r + 1] = tonumber(c.rarities[j]) end end)
            info.chains[#info.chains + 1] = { rarities = r, matched = tonumber(c.matchedValues), owner = tonumber(c.ownerId),
                                              fulfilled = c.isFulfilled and true or false, possible = c.isPossible and true or false }
        end
    end)
    pcall(function()
        local progs = breachCtrl:GetUnlockablePrograms()
        info.programs = {}
        for i = 1, #progs do
            local p = progs[i]
            info.programs[#info.programs + 1] = { name = tostring(p.name), fulfilled = p.isFulfilled and true or false, hidden = p.hidden and true or false }
        end
    end)
    -- widgets
    local okW, errW = pcall(function()
        local root = breachCtrl:GetRootWidget()
        if not root then info.reason = 'pas de widget racine'; return end
        local rs = root:GetSize()
        info.root = { w = rs.X, h = rs.Y }
        local texts, allTexts, count = {}, {}, 0
        local dump = (cmd and cmd.x == 1)
        local function walk(w, ax, ay, depth, pw, ph)
            if depth > 40 or count > 6000 then return end
            local okC, isC = pcall(function() return w:IsA('inkCompoundWidget') end)
            if not (okC and isC) then return end
            local n = 0
            pcall(function() n = w:GetNumChildren() end)
            for i = 0, n - 1 do
                local c = nil
                pcall(function() c = w:GetWidget(i) end)
                if c then
                    count = count + 1
                    local px, py, sw, sh, tx, ty = 0, 0, 0, 0, 0, 0
                    pcall(function() local p = w:GetChildPosition(c); px, py = p.X, p.Y end)
                    pcall(function() local sz = w:GetChildSize(c); sw, sh = sz.X, sz.Y end)
                    pcall(function() local t = c:GetTranslation(); tx, ty = t.X, t.Y end)
                    local cx, cy = ax + px + tx, ay + py + ty
                    local okT, isT = pcall(function() return c:IsA('inkTextWidget') end)
                    if okT and isT then
                        local txt = ''
                        pcall(function() txt = tostring(c:GetText()) end)
                        local vis = true
                        pcall(function() vis = c:IsVisible() end)
                        local up = txt:upper():gsub('^%s+', ''):gsub('%s+$', '')
                        if up:match('^[0-9A-F][0-9A-F]$') then
                            texts[#texts + 1] = { t = up, x = cx, y = cy, w = sw, h = sh, px = ax, py = ay, pw = pw or 0, ph = ph or 0, v = vis, d = depth }
                        end
                        if dump and #allTexts < 200 then
                            local nm = ''
                            pcall(function() nm = tostring(c:GetName()) end)
                            allTexts[#allTexts + 1] = string.format('%s|%s|%.0f,%.0f|%.0fx%.0f|p%.0fx%.0f|%s|d%d', nm, up:sub(1, 24), cx, cy, sw, sh, pw or 0, ph or 0, tostring(vis), depth)
                        end
                    end
                    walk(c, cx, cy, depth + 1, sw, sh)
                end
            end
        end
        walk(root, 0, 0, 0, rs.X, rs.Y)
        info.texts, info.widgets = texts, count
        if dump then
            journal(string.format('BREACH dump : racine %.0fx%.0f, %d widgets, %d textes', rs.X, rs.Y, count, #allTexts))
            for _, l in ipairs(allTexts) do journal('BREACH txt ' .. l) end
        end
    end)
    if not okW then info.reason = 'widgets : ' .. tostring(errW) end
    info.ok = (info.texts ~= nil and #info.texts > 0)
    if not info.ok and not info.reason then info.reason = 'aucun texte hexadecimal trouve' end
    return info
end

registerForEvent('onInit', function()
    os.remove('probe_progress.txt')
    journal('onInit ' .. os.date('%H:%M:%S'))
    -- Breach Protocol : on capture l instance du controleur natif a l ouverture (grille/sequences lisibles ensuite)
    local okO, errO = pcall(function()
        Observe('HackingMinigameGameController', 'OnInitialize', function(self)
            breachCtrl = self
            journal('BREACH ouvert : controleur capture')
        end)
        Observe('HackingMinigameGameController', 'OnUninitialize', function(self)
            breachCtrl = nil
            journal('BREACH ferme')
        end)
    end)
    journal('BREACH hooks : ' .. tostring(okO) .. (okO and '' or (' ' .. tostring(errO))))
    fh = io.open('state.bin', 'w+b')
    journal('state.bin ouvert : ' .. tostring(fh ~= nil))
    -- table de commandes SQLite (canal Python -> Lua). NB: db:exec = SQLite, pas un shell.
    local okDb, errDb = pcall(function()
        db:exec('CREATE TABLE IF NOT EXISTS cmd (seq INTEGER PRIMARY KEY, cmd TEXT, x REAL, y REAL, z REAL, hash INTEGER)')
        pcall(function() db:exec('ALTER TABLE cmd ADD COLUMN hash INTEGER') end)
        -- une commande restee dans la table depuis une session precedente ne doit JAMAIS etre rejouee
        local n = 0
        for row in db:nrows('SELECT MAX(seq) AS m FROM cmd') do if row.m then lastCmdSeq = row.m; n = 1 end end
        db:exec('DELETE FROM cmd')
        return n
    end)
    dbReady = okDb
    journal(string.format('sqlite db : type=%s pret=%s %s', type(db), tostring(okDb), okDb and '' or tostring(errDb)))
    print('[AgentProbe] v2 pret (sondes apres ' .. WARMUP .. ' s de jeu)')
end)

registerForEvent('onUpdate', function(dt)
    local player = Game.GetPlayer()
    if not player then attachedFor = 0.0; return end
    -- pendant le chargement, le joueur existe mais reste a l'origine : on attend
    -- qu'il soit reellement place dans le monde avant de compter l'echauffement
    local p0 = player:GetWorldPosition()
    if math.abs(p0.x) + math.abs(p0.y) < 10.0 then attachedFor = 0.0; return end
    attachedFor = attachedFor + dt
    lastUpdateClock = os.clock()
    if attachedFor < WARMUP then return end

    -- une sonde par image, apres l'echauffement
    if not probesDone then runNextProbe() end

    -- commandes Python (chemins), des que le monde est pret.
    -- Une erreur ici est JOURNALISEE (une fois par message) : plus d echec silencieux.
    local okP, errP = pcall(pollCommands, player, dt)
    if not okP and tostring(errP) ~= lastPollErr then
        lastPollErr = tostring(errP)
        journal('POLL erreur: ' .. lastPollErr)
    end

    -- diagnostic repete toutes les VITALS_PERIOD s : navmesh / marqueurs / chemin
    vitalsAcc = vitalsAcc + dt
    if probesDone and vitalsAcc >= VITALS_PERIOD then
        vitalsAcc = 0.0
        local ok, line = pcall(function()
            local pos = player:GetWorldPosition()
            local ns, ai, ms, jm = Game.GetNavigationSystem(), Game.GetAINavigationSystem(), Game.GetMappinSystem(), Game.GetJournalManager()
            local streamed = ns:IsNavmeshStreamedInLocation(pos, 1.0)
            local onNav = ai:IsPointOnNavmesh(player, pos, 1.0)
            local entries = ms:GetMappinEntries(gamemappinsMappinTargetType.Minimap)
            local nMap = type(entries) == 'table' and #entries or -1
            local e = jm:GetTrackedEntry()
            local dMap = e and jm:GetDistanceToNearestMappin(e) or -2
            local fwd = player:GetWorldForward()
            local dst = Vector4.new(pos.x + fwd.x * 12, pos.y + fwd.y * 12, pos.z, 1.0)
            local path = ai:CalculatePathForCharacter(pos, dst, 2.0, player)
            local nPath = path and #path.path or 0
            return string.format('VITALS t=%.0fs pos=%.0f,%.0f,%.0f streamed=%s onNav=%s mappins=%d distMappin=%.0f pathAhead=%d',
                attachedFor, pos.x, pos.y, pos.z, tostring(streamed), tostring(onNav), nMap, dMap, nPath)
        end)
        journal(ok and line or ('VITALS erreur: ' .. tostring(line)))
    end

    -- canal d'etat minimal : position + cap
    if not fh then return end
    acc = acc + dt
    if acc < SAMPLE_PERIOD then return end
    acc = 0.0
    local ok, data = pcall(function()
        local pos = player:GetWorldPosition()
        local id = player:GetEntityID()
        local defs = GetAllBlackboardDefs()
        -- vie (%) et combat (verifies par sonde)
        local hp = Game.GetStatPoolsSystem():GetStatPoolValue(id, gamedataStatPoolType.Health, true)
        local playerLevel = nil
        pcall(function() playerLevel = Game.GetStatsSystem():GetStatValue(id, gamedataStatType.Level) end)
        -- NAGE : V dans l eau (IsSwimming) et oxygene restant (plongee) : ne jamais se noyer
        local swim, oxygen = nil, nil
        pcall(function() swim = player:IsSwimming() end)
        pcall(function() oxygen = Game.GetStatPoolsSystem():GetStatPoolValue(id, gamedataStatPoolType.Oxygen, true) end)
        local psm = Game.GetBlackboardSystem():GetLocalInstanced(id, defs.PlayerStateMachine)
        local inCombat = psm and (psm:GetInt(defs.PlayerStateMachine.Combat) == EnumInt(gamePSMCombat.InCombat)) or false
        local inVehicle = false
        pcall(function() inVehicle = psm and (psm:GetInt(defs.PlayerStateMachine.Vehicle) > 0) or false end)
        -- etats qui EMPECHENT ou ralentissent le deplacement : corps porte, accroupi, etc.
        local carrying, locomotion, upperBody = false, -1, -1
        pcall(function() carrying = psm and (psm:GetInt(defs.PlayerStateMachine.BodyCarrying) > 0) or false end)
        pcall(function() locomotion = psm and psm:GetInt(defs.PlayerStateMachine.Locomotion) or -1 end)
        pcall(function() upperBody = psm and psm:GetInt(defs.PlayerStateMachine.UpperBody) or -1 end)
        local lootPanel = false
        local lootCount = 0
        pcall(function()
            local ld = FromVariant(Game.GetBlackboardSystem():Get(defs.UIInteractions):GetVariant(defs.UIInteractions.LootData))
            lootPanel = (ld ~= nil and ld.itemIDs ~= nil and #ld.itemIDs > 0)
            lootCount = (ld ~= nil and ld.itemIDs ~= nil) and #ld.itemIDs or 0
        end)
        -- dialogue : hubs de choix (blackboard UIInteractions, verifie par sonde)
        local dlg = nil
        local ui = Game.GetBlackboardSystem():Get(defs.UIInteractions)
        local hubs = FromVariant(ui:GetVariant(defs.UIInteractions.DialogChoiceHubs))
        if hubs and hubs.choiceHubs and #hubs.choiceHubs > 0 then
            local list, inactiveList = {}, {}
            for i = 1, #hubs.choiceHubs do
                local hub = hubs.choiceHubs[i]
                for j = 1, #hub.choices do
                    local c = hub.choices[j]
                    list[#list + 1] = tostring(c.localizedName)
                    -- choix grise (Inactive : argent/competence insuffisants) -> le jeu le saute
                    local inactive = false
                    pcall(function() inactive = ChoiceTypeWrapper.IsType(c.type, gameinteractionsChoiceType.Inactive) end)
                    inactiveList[#inactiveList + 1] = inactive and 1 or 0
                end
            end
            dlg = { title = tostring(hubs.choiceHubs[1].title), choices = list, inactive = inactiveList,
                    sel = ui:GetInt(defs.UIInteractions.SelectedIndex), hubs = #hubs.choiceHubs }
        end
        -- invite d interaction dans le monde ("Parler", "Ouvrir"...) : hub unique
        local inter = nil
        pcall(function()
            local hub = FromVariant(ui:GetVariant(defs.UIInteractions.InteractionChoiceHub))
            if hub and hub.choices and #hub.choices > 0 then
                local list = {}
                for j = 1, #hub.choices do list[#list + 1] = GetLocalizedText(tostring(hub.choices[j].localizedName)) end
                inter = { title = tostring(hub.title), choices = list }
                pcall(function() inter.active = hub.active end)
                pcall(function() inter.id = tostring(hub.id) end)
            end
        end)
        -- objectif suivi : texte + presence d un marqueur (pour le cerveau)
        local quest = nil
        pcall(function()
            local jm = Game.GetJournalManager()
            local e = jm:GetTrackedEntry()
            if e then
                local m = Game.GetMappinSystem():GetMappinFromObjective(jm:GetParentEntry(e), e)
                local mp = m and m:GetWorldPosition() or nil
                quest = { text = GetLocalizedText(tostring(e:GetDescription())):sub(1, 80),
                          hash = jm:GetEntryHash(e),
                          hasMappin = m ~= nil,
                          mx = mp and mp.x or nil, my = mp and mp.y or nil, mz = mp and mp.z or nil }
                pcall(function() quest.lvl = questLevelOf(jm, e, quest.hash) end)
            end
        end)
        -- ennemis hostiles (TargetTrackerComponent, sonde OK) : position, distance, cap,
        -- et projection ecran (pour viser en boucle fermee). 6 plus proches.
        local enemies = nil
        -- UNE seule methode, verifiee en jeu : TSQ_EnemyNPC (GetHostileThreats renvoyait vide en
        -- combat -> V ne voyait aucune cible et ne faisait que se soigner, 2026-09-10 20:18).
        -- Portee 60 m en combat, 40 m hors combat. Diagnostic journalise si vide en combat.
        do
            local okE, errE = pcall(function()
                local ts = Game.GetTargetingSystem()
                local range = inCombat and 60.0 or 40.0
                -- 1) PNJ hostiles, SANS contrainte de visibilite (en combat ils sont souvent
                --    hors champ : le filtre par defaut renvoyait parts=0)
                local q = Game['TSQ_EnemyNPC;']()
                q.maxDistance = range
                q.filterObjectByDistance = true
                pcall(function() q.testedSet = TargetingSet.Complete end)
                local okT, parts = ts:GetTargetParts(player, q)
                local nEnemy = (okT and parts) and #parts or 0
                -- 2) filet : TOUT ce qui est ciblable, filtre par attitude hostile envers V
                local nAll, nHostile = 0, 0
                if nEnemy == 0 then
                    local qa = Game['TSQ_ALL;']()
                    qa.maxDistance = range
                    qa.filterObjectByDistance = true
                    pcall(function() qa.testedSet = TargetingSet.Complete end)
                    local okA, partsA = ts:GetTargetParts(player, qa)
                    if okA and partsA then
                        nAll = #partsA
                        local kept = {}
                        local seenEnt = {}   -- une entree par ENTITE (GetTargetParts renvoie une partie par zone du corps)
                        for i = 1, #partsA do
                            local comp = TS_TargetPartInfo.GetComponent(partsA[i])
                            local ent = comp and comp:GetEntity() or nil
                            if ent then
                                local okH, h = pcall(function() return ent:GetEntityID().hash end)
                                local key = okH and tostring(h) or tostring(ent)
                                if seenEnt[key] then ent = nil else seenEnt[key] = true end
                            end
                            if ent then
                                local hostile = false
                                pcall(function()
                                    hostile = (ent:GetAttitudeTowards(player) == EAIAttitude.AIA_Hostile)
                                end)
                                if hostile then kept[#kept + 1] = partsA[i] end
                            end
                        end
                        nHostile = #kept
                        if nHostile > 0 then parts, okT = kept, true end
                    end
                end
                enemyDiag = string.format('enemyNPC=%d all=%d hostiles=%d', nEnemy, nAll, nHostile)
                if okT and parts and #parts > 0 then
                    local cam = Game.GetCameraSystem()
                    local list = {}
                    local seenEnt = {}   -- une entree par ENTITE (GetTargetParts renvoie une partie par zone du corps)
                    for i = 1, #parts do
                        local comp = TS_TargetPartInfo.GetComponent(parts[i])
                        local ent = comp and comp:GetEntity() or nil
                        if ent then
                            local okH, h = pcall(function() return ent:GetEntityID().hash end)
                            local key = okH and tostring(h) or tostring(ent)
                            if seenEnt[key] then ent = nil else seenEnt[key] = true end
                        end
                        if ent then
                            local ep = ent:GetWorldPosition()
                            local dxE, dyE = ep.x - pos.x, ep.y - pos.y
                            local rec = { x = ep.x, y = ep.y, z = ep.z, d = math.sqrt(dxE * dxE + dyE * dyE), id = key }
                            -- police (NCPD / MaxTac) : V ne l engage jamais de lui-meme, il ne fait que se defendre
                            pcall(function()
                                local aff = tostring(TweakDBInterface.GetCharacterRecord(ent:GetRecordID()):Affiliation():Type())
                                if aff:find('NCPD') or aff:find('MaxTac') or aff:find('Police') then rec.police = true end
                            end)
                            pcall(function() if not rec.police and ent:IsPolice() then rec.police = true end end)
                            pcall(function()
                                local sc = cam:ProjectPoint(Vector4.new(ep.x, ep.y, ep.z + 1.3, 1.0))
                                rec.sx, rec.sy = sc.x, sc.y
                            end)
                            pcall(function() rec.dead = ent:IsDead() end)
                            list[#list + 1] = rec
                        end
                    end
                    table.sort(list, function(a, b) return a.d < b.d end)
                    while #list > 6 do table.remove(list) end
                    if #list > 0 then enemies = list end
                end
            end)
            if not okE then enemyDiag = 'erreur: ' .. tostring(errE) end
            if inCombat and not enemies and enemyDiag ~= lastEnemyDiag then
                lastEnemyDiag = enemyDiag
                journal('ENNEMIS vides en combat : ' .. enemyDiag)
            end
        end
        if false then
            pcall(function()
                local threats = player:GetTargetTrackerComponent():GetHostileThreats(false)
                if threats and #threats > 0 then
                    local cam = Game.GetCameraSystem()
                    local list = {}
                    for i = 1, #threats do
                        local ent = threats[i].entity
                        if ent then
                            local ep = ent:GetWorldPosition()
                            local dxE, dyE = ep.x - pos.x, ep.y - pos.y
                            local rec = { x = ep.x, y = ep.y, z = ep.z, d = math.sqrt(dxE * dxE + dyE * dyE) }
                            pcall(function()
                                local sc = cam:ProjectPoint(Vector4.new(ep.x, ep.y, ep.z + 1.3, 1.0))
                                rec.sx, rec.sy = sc.x, sc.y
                            end)
                            pcall(function() rec.dead = ent:IsDead() end)
                            list[#list + 1] = rec
                        end
                    end
                    table.sort(list, function(a, b) return a.d < b.d end)
                    while #list > 6 do table.remove(list) end
                    enemies = list
                end
            end)
        end
        -- panneau quickhack (si le blackboard existe) : ouvert ? index selectionne ?
        -- panneau quickhack (blackboards sondes le 2026-09-11) : ouvert ? hack surligne ?
        -- liste des hacks (PlayerQuickHackData.CachedQuickHackList) + RAM disponible
        local qh = nil
        pcall(function()
            local d = defs.UI_QuickSlotsData
            if not d then return end
            local bb2 = Game.GetBlackboardSystem():Get(d)
            local open = bb2:GetBool(d.quickhackPanelOpen)
            if not open then return end
            qh = { open = true }
            pcall(function()
                local sel = FromVariant(bb2:GetVariant(d.quickHackDataSelected))
                if sel then qh.sel = tostring(sel.actionRecord):gsub('^.-%.', '') end
            end)
            pcall(function()
                local pd = defs.PlayerQuickHackData
                local bb3 = Game.GetBlackboardSystem():Get(pd)
                local lst = FromVariant(bb3:GetVariant(pd.CachedQuickHackList))
                if lst and #lst > 0 then
                    local list = {}
                    for i = 1, math.min(#lst, 12) do
                        local h = lst[i]
                        local rec = { i = i }
                        pcall(function() rec.action = tostring(h.actionRecord):gsub('^.-%.', '') end)   -- ex. OverheatHack
                        pcall(function() rec.title = tostring(GetLocalizedTextByKey(TweakDBInterface.GetItemRecord(h.itemID.id):DisplayName())) end)
                        pcall(function() rec.quality = h.quality end)
                        list[#list + 1] = rec
                    end
                    qh.list = list
                    if not qhListLogged then
                        qhListLogged = true
                        journal('QHLIST ' .. json.encode(list):sub(1, 900))
                        -- les champs m_title etaient nil : on dumpe la structure reelle du 1er element
                        pcall(function() journal('QHDUMP ' .. GameDump(lst[1]):gsub('%s+', ' '):sub(1, 1200)) end)
                        pcall(function() journal('QHTYPE ' .. tostring(lst[1]) .. ' / ' .. type(lst[1])) end)
                    end
                end
            end)
            pcall(function() qh.ram = Game.GetStatPoolsSystem():GetStatPoolValue(id, gamedataStatPoolType.Memory, false) end)
        end)
        -- OBJETS LOOTABLES a < 20 m : conteneurs, objets au sol, corps (par nom de classe), avec
        -- projection ecran pour viser en hauteur. 8 plus proches.
        local loot = nil
        local slowScan = (attachedFor - slowT) >= 0.25      -- scans larges (couteux) : 4 fois par seconde suffisent
        if slowScan then slowT = attachedFor end
        if not inCombat and slowScan then
            pcall(function()
                local qa = Game['TSQ_ALL;']()
                qa.maxDistance = 20.0
                qa.filterObjectByDistance = true
                pcall(function() qa.testedSet = TargetingSet.Complete end)
                local okA, partsA = Game.GetTargetingSystem():GetTargetParts(player, qa)
                if okA and partsA and #partsA > 0 then
                    local cam = Game.GetCameraSystem()
                    local list = {}
                    local seenEnt = {}   -- une entree par ENTITE (GetTargetParts renvoie une partie par zone du corps)
                    for i = 1, #partsA do
                        local comp = TS_TargetPartInfo.GetComponent(partsA[i])
                        local ent = comp and comp:GetEntity() or nil
                        if ent then
                            local okH, h = pcall(function() return ent:GetEntityID().hash end)
                            local key = okH and tostring(h) or tostring(ent)
                            if seenEnt[key] then ent = nil else seenEnt[key] = true end
                        end
                        if ent then
                            -- classe : par IsA (fiable), le nom de classe n etant pas toujours resolvable
                            local cls = ''
                            for _, cn in ipairs({ 'gameLootContainerBase', 'gameItemDropObject', 'gameLootBag', 'gameContainerObject',
                                                  'gameDevice', 'gamePuppet', 'gameObject' }) do
                                local okC, isIt = pcall(function() return ent:IsA(cn) end)
                                if okC and isIt then cls = cn; break end
                            end
                            local isLoot = (cls == 'gameLootContainerBase' or cls == 'gameItemDropObject' or cls == 'gameLootBag' or cls == 'gameContainerObject')
                            local dead = false
                            if not isLoot then pcall(function() dead = ent:IsDead() end) end
                            -- un dispositif (porte, terminal) n est pas du loot ; un objet mort ou un puppet mort, oui
                            if cls == 'gameDevice' and not isLoot then dead = false end
                            if cls == 'gameObject' and not isLoot and not dead then dead = false end
                            if isLoot or dead then
                                local ep = ent:GetWorldPosition()
                                local rec = { x = ep.x, y = ep.y, z = ep.z, cls = cls:sub(1, 24),
                                              d = math.sqrt((ep.x - pos.x) ^ 2 + (ep.y - pos.y) ^ 2) }
                                pcall(function()
                                    local sc = cam:ProjectPoint(Vector4.new(ep.x, ep.y, ep.z + 0.4, 1.0))
                                    rec.sx, rec.sy = sc.x, sc.y
                                end)
                                list[#list + 1] = rec
                            end
                        end
                    end
                    table.sort(list, function(a, b) return a.d < b.d end)
                    while #list > 8 do table.remove(list) end
                    if #list > 0 then loot = list end
                    slowLoot = loot
                    if not lootClassesLogged and #list > 0 then
                        lootClassesLogged = true
                        local names = {}
                        for i = 1, #list do names[#names + 1] = list[i].cls end
                        journal('LOOTCLS ' .. table.concat(names, ','))
                    end
                end
            end)
        end
        -- AGRESSIONS signalees (marqueurs NCPD : assault / hustle / crime / gang) a < 80 m, toutes les 2 s
        crimeAcc = crimeAcc + SAMPLE_PERIOD
        if crimeAcc >= 2.0 then
            crimeAcc = 0.0
            pcall(function()
                local out = {}
                local mappins = Game.GetMappinSystem():GetMappins(gamemappinsMappinTargetType.Minimap)
                if type(mappins) == 'table' then
                    for i = 1, #mappins do
                        local v = ''
                        pcall(function() v = tostring(mappins[i]:GetVariant()):gsub('gamedataMappinVariant : ', ''):gsub(' %(%d+%)', '') end)
                        local lv = v:lower()
                        if lv:find('assault') or lv:find('hustle') or lv:find('crime') or lv:find('gangwatch') or lv:find('psycho') then
                            local w = mappins[i]:GetWorldPosition()
                            local dd = math.sqrt((w.x - pos.x) ^ 2 + (w.y - pos.y) ^ 2)
                            if dd < 80 then out[#out + 1] = { variant = v, x = w.x, y = w.y, z = w.z, d = dd } end
                        end
                    end
                end
                table.sort(out, function(a, b) return a.d < b.d end)
                lastCrimes = (#out > 0) and out or nil
            end)
        end
        -- ARME ACTIVE (main droite) : type TweakDB, pour que le combat sache ce que V tient vraiment
        local weapon = nil
        pcall(function()
            local ts = Game.GetTransactionSystem()
            local obj = ts:GetItemInSlot(player, TweakDBID.new('AttachmentSlots.WeaponRight'))
            if obj then
                local wid = obj:GetItemID()
                weapon = tostring(TweakDBInterface.GetItemRecord(ItemID.GetTDBID(wid)):ItemType():Type()):gsub('gamedataItemType : ', ''):gsub(' %(%d+%)', '')
            end
        end)
        -- BREACH PROTOCOL (mini-jeu de piratage d un terminal / point d acces) : State + temps restant, pour que V
        -- ne reste pas devant la grille indefiniment (pas encore de resolveur : il en sort)
        local breach = nil
        pcall(function()
            local stt, tm = breachState()
            if stt and stt ~= 0 then breach = { state = stt, timer = tm, last = breachLastPos(), ctrl = (breachCtrl ~= nil) } end
        end)
        -- TELEPHONE : appel entrant / en cours (UI_ComDevice.callInformation : callPhase, contactName)
        local phone = nil
        pcall(function()
            local def = GetAllBlackboardDefs().UI_ComDevice
            if not def then return end
            local bb = Game.GetBlackboardSystem():Get(def)
            if not bb then return end
            local p = {}
            pcall(function()
                local ci = bb:GetVariant(def.PhoneCallInformation)     -- nom exact d apres GameDump(UI_ComDevice)
                if ci then
                    local info = FromVariant(ci)
                    if info then
                        local ph = tostring(info.callPhase)
                        p.phase = ph
                        p.incoming = ph:find('Incoming') ~= nil
                        p.active = ph:find('StartCall') ~= nil or ph:find('Start') ~= nil
                        pcall(function() p.contact = GetLocalizedText(tostring(info.contactName)) end)
                    end
                end
            end)
            pcall(function() p.contacts = bb:GetBool(def.ContactsActive) end)
            pcall(function() p.sms_open = bb:GetBool(def.SmsMessengerActive) end)
            pcall(function() p.msg_shown = bb:GetBool(def.isDisplayingMessage) end)
            pcall(function() p.msg_hash = bb:GetInt(def.MessageToOpenHash) end)       -- change quand un SMS arrive
            pcall(function() p.enabled = bb:GetBool(def.PhoneEnabled) end)
            if next(p) ~= nil then phone = p end
        end)
        -- BUFFS actifs (StatusEffectSystem) : nourri (regen vie), hydrate (regen endurance), drogue de combat
        local buffs = nil
        pcall(function()
            local ses = Game.GetStatusEffectSystem()
            local pid = player:GetEntityID()
            local b = {}
            b.n = ses:HasStatusEffect(pid, TweakDBID.new('BaseStatusEffect.Nourished')) and 1 or 0
            b.h = ses:HasStatusEffect(pid, TweakDBID.new('BaseStatusEffect.Hydrated')) and 1 or 0
            b.d = (ses:HasStatusEffect(pid, TweakDBID.new('BaseStatusEffect.BlackLace')) or ses:HasStatusEffect(pid, TweakDBID.new('BaseStatusEffect.BlackLaceV0'))) and 1 or 0
            buffs = b
        end)
        -- VEHICULES proches (< 25 m) : pour monter dans la voiture appelee
        local vehicles = nil
        if not inCombat and slowScan then
            pcall(function()
                local qv = Game['TSQ_ALL;']()
                qv.maxDistance = 150.0
                qv.filterObjectByDistance = true
                pcall(function() qv.testedSet = TargetingSet.Complete end)
                local okV, partsV = Game.GetTargetingSystem():GetTargetParts(player, qv)
                if okV and partsV and #partsV > 0 then
                    local list, seenV = {}, {}
                    for i = 1, #partsV do
                        local comp = TS_TargetPartInfo.GetComponent(partsV[i])
                        local ent = comp and comp:GetEntity() or nil
                        if ent then
                            local okI, isV = pcall(function() return ent:IsA('vehicleBaseObject') end)
                            if okI and isV then
                                local okH, h = pcall(function() return ent:GetEntityID().hash end)
                                local key = okH and tostring(h) or tostring(ent)
                                if not seenV[key] then
                                    seenV[key] = true
                                    local vp = ent:GetWorldPosition()
                                    local rec = { x = vp.x, y = vp.y, z = vp.z, d = math.sqrt((vp.x - pos.x) ^ 2 + (vp.y - pos.y) ^ 2) }
                                    pcall(function() rec.name = GetLocalizedText(tostring(ent:GetDisplayName())) end)
                                    pcall(function() rec.player = ent:IsPlayerVehicle() end)
                                    if rec.player or rec.d <= 25.0 then list[#list + 1] = rec end   -- les voitures des inconnus : 25 m ; la sienne : 150 m
                                end
                            end
                        end
                    end
                    table.sort(list, function(a, b) return a.d < b.d end)
                    while #list > 4 do table.remove(list) end
                    if #list > 0 then vehicles = list end
                    slowVehicles = vehicles
                end
            end)
        end
        -- OBJET SOUS LE RETICULE : classe + distance (pour savoir quand V regarde un conteneur)
        local lookat = nil
        pcall(function()
            local ts2 = Game.GetTargetingSystem()
            local obj, how = nil, ''
            local ok1, o1 = pcall(function() return ts2:GetLookAtObject(player, true, false) end)
            if ok1 and o1 then obj, how = o1, 'GetLookAtObject(true,false)' end
            if not obj then local ok2, o2 = pcall(function() return ts2:GetLookAtObject(player, false, false) end); if ok2 and o2 then obj, how = o2, 'GetLookAtObject(false,false)' end end
            if not obj then local ok3, o3 = pcall(function() return ts2:GetObjectClosestToCrosshair(player) end); if ok3 and o3 then obj, how = o3, 'GetObjectClosestToCrosshair' end end
            if obj and not lookatLogged then lookatLogged = true; journal('LOOKAT methode : ' .. how) end
            if obj then
                local cls = 'autre'
                for _, cn in ipairs({ 'gameLootContainerBase', 'gameItemDropObject', 'gameLootBag', 'gameContainerObject',
                                      'gameDevice', 'gamePuppet', 'vehicleBaseObject', 'gameObject' }) do
                    local okC, isIt = pcall(function() return obj:IsA(cn) end)
                    if okC and isIt then cls = cn; break end
                end
                local op = obj:GetWorldPosition()
                lookat = { cls = cls, d = math.sqrt((op.x - pos.x) ^ 2 + (op.y - pos.y) ^ 2) }
                pcall(function() lookat.dead = obj:IsDead() end)
            end
        end)
        -- PNJ NON HOSTILES proches (pour engager une conversation de sa propre initiative) :
        -- TSQ_NPC (sonde OK) a 15 m, attitude non hostile, avec leur nom affiche. 5 max.
        local npcs = nil
        if not inCombat and slowScan then
            pcall(function()
                local q = Game['TSQ_NPC;']()
                q.maxDistance = 30.0
                q.filterObjectByDistance = true
                pcall(function() q.testedSet = TargetingSet.Complete end)
                local okN, parts = Game.GetTargetingSystem():GetTargetParts(player, q)
                if okN and parts and #parts > 0 then
                    local list = {}
                    local seenEnt = {}   -- une entree par ENTITE (GetTargetParts renvoie une partie par zone du corps)
                    for i = 1, #parts do
                        local comp = TS_TargetPartInfo.GetComponent(parts[i])
                        local ent = comp and comp:GetEntity() or nil
                        if ent then
                            local okH, h = pcall(function() return ent:GetEntityID().hash end)
                            local key = okH and tostring(h) or tostring(ent)
                            if seenEnt[key] then ent = nil else seenEnt[key] = true end
                        end
                        if ent then
                            local hostile, dead = false, false
                            pcall(function() hostile = (ent:GetAttitudeTowards(player) == EAIAttitude.AIA_Hostile) end)
                            pcall(function() dead = ent:IsDead() end)
                            if not hostile and not dead then
                                local ep = ent:GetWorldPosition()
                                local rec = { x = ep.x, y = ep.y, z = ep.z,
                                              d = math.sqrt((ep.x - pos.x) ^ 2 + (ep.y - pos.y) ^ 2) }
                                pcall(function() rec.name = GetLocalizedText(tostring(ent:GetDisplayName())) end)
                                pcall(function() rec.aggressive = ent:IsAggressive() end)
                                pcall(function() rec.incombat = ent:IsInCombat() end)
                                pcall(function()
                                    local aff = tostring(TweakDBInterface.GetCharacterRecord(ent:GetRecordID()):Affiliation():Type())
                                    rec.aff = aff:gsub('gamedataAffiliation : ', ''):gsub(' %(%d+%)', '')
                                end)
                                list[#list + 1] = rec
                            end
                        end
                    end
                    table.sort(list, function(a, b) return a.d < b.d end)
                    while #list > 5 do table.remove(list) end
                    if #list > 0 then npcs = list end
                    slowNpcs = npcs
                end
            end)
        end
        -- DANSE SENSORIELLE : etat + indices (entites a 4 Hz) + objet sous le reticule
        local bd = bdState()
        if bd then
            if slowScan or not bdFocusCache then
                pcall(function() bdFocusCache = bdScanFocus(player, pos) end)
            end
            local focus = {}
            for i = 1, math.min(8, #(bdFocusCache or {})) do focus[i] = bdFocusCache[i] end
            bd.focus = focus
            pcall(function()
                local ts2 = Game.GetTargetingSystem()
                local obj = nil
                pcall(function() obj = ts2:GetLookAtObject(player, true, false) end)
                if not obj then pcall(function() obj = ts2:GetLookAtObject(player, false, false) end) end
                if obj then
                    local sc = nil
                    pcall(function() sc = obj:FindComponentByName('scanning') end)
                    if sc then
                        local l = {}
                        pcall(function() l.clue = sc:IsBraindanceClue() end)
                        pcall(function() l.scanned = sc:IsScanned() end)
                        pcall(function() l.prog = sc:GetScanningProgress() end)
                        pcall(function() l.blocked = sc:IsBraindanceBlocked() end)
                        pcall(function() l.scanning = sc:IsScanning() end)
                        local op = obj:GetWorldPosition()
                        l.d = math.sqrt((op.x - pos.x) ^ 2 + (op.y - pos.y) ^ 2 + (op.z - pos.z) ^ 2)
                        pcall(function() l.id = tostring(obj:GetEntityID().hash) end)
                        bd.look = l
                    end
                end
            end)
        end
        if not inCombat and not slowScan then loot, vehicles, npcs = slowLoot, slowVehicles, slowNpcs end
        if inCombat then slowLoot, slowVehicles, slowNpcs = nil, nil, nil end
        -- CORPS : les requetes de ciblage excluent souvent les morts. On memorise la derniere
        -- position de chaque ennemi vu (cle = position arrondie) ; quand il n est plus
        -- renvoye vivant, il devient un "corps" exporte pendant 90 s (pour le loot).
        local nowT = attachedFor
        local seen = {}
        if enemies then
            for i = 1, #enemies do
                local e = enemies[i]
                local key = e.id or string.format('%d:%d', math.floor(e.x / 2), math.floor(e.y / 2))
                e.id = nil                                      -- interne : pas exporte
                seen[key] = true
                if e.dead then
                    bodyMemory[key] = { x = e.x, y = e.y, z = e.z, t = nowT }
                elseif not bodyMemory[key] then
                    aliveMemory[key] = { x = e.x, y = e.y, z = e.z, t = nowT }
                end
            end
        end
        for key, rec in pairs(aliveMemory) do
            if not seen[key] and nowT - rec.t > 1.5 then     -- disparu des vivants depuis 1,5 s
                bodyMemory[key] = { x = rec.x, y = rec.y, z = rec.z, t = nowT }
                aliveMemory[key] = nil
            elseif nowT - rec.t > 30 then
                aliveMemory[key] = nil
            end
        end
        local bodies = {}
        for key, rec in pairs(bodyMemory) do
            if nowT - rec.t > 90 then bodyMemory[key] = nil
            else
                local dB = math.sqrt((rec.x - pos.x) ^ 2 + (rec.y - pos.y) ^ 2)
                bodies[#bodies + 1] = { x = rec.x, y = rec.y, z = rec.z, d = dB }
            end
        end
        table.sort(bodies, function(a, b) return a.d < b.d end)
        while #bodies > 8 do table.remove(bodies) end
        if #bodies == 0 then bodies = nil end
        seq = seq + 1
        return { seq = seq, x = pos.x, y = pos.y, z = pos.z, yaw = player:GetWorldYaw(),
                 hp = hp, level = playerLevel, swim = swim, oxygen = oxygen, combat = inCombat, vehicle = inVehicle, carrying = carrying, locomotion = locomotion, upperBody = upperBody,
                 lootPanel = lootPanel, lootCount = lootCount, loot = loot, lookat = lookat, crimes = lastCrimes, vehicles = vehicles, buffs = buffs, phone = phone, breach = breach, weapon = weapon,
                 enemies = enemies, bodies = bodies, npcs = npcs, qh = qh, dialog = dlg, interact = inter, quest = quest, bd = bd, seqEnd = seq }
    end)
    -- journal une fois par changement de dialogue : structure reelle des hubs (pour la competence)
    if ok and data then
        local sig = data.dialog and (data.dialog.title .. '|' .. #data.dialog.choices .. '|' .. tostring(data.dialog.sel)) or ''
        if sig ~= lastDialogSig then
            lastDialogSig = sig
            if data.dialog then journal('DIALOG ' .. json.encode(data.dialog)) else journal('DIALOG ferme') end
        end
    end
    if not ok and tostring(data) ~= lastStateErr then
        lastStateErr = tostring(data)
        journal('STATE erreur: ' .. lastStateErr)
    end
    if ok and data then
        local okE, s = pcall(json.encode, data)
        if not okE then
            if tostring(s) ~= lastStateErr then lastStateErr = tostring(s); journal('STATE encode: ' .. lastStateErr) end
            s = nil
        end
        if s and #s >= STATE_WIDTH then
            -- degradation ordonnee : on retire le moins utile jusqu a tenir dans la largeur fixe
            journal(string.format('STATE trop long : %d o > %d (loot=%s npcs=%s veh=%s crimes=%s)', #s, STATE_WIDTH,
                tostring(data.loot and #data.loot), tostring(data.npcs and #data.npcs), tostring(data.vehicles and #data.vehicles), tostring(data.crimes and #data.crimes)))
            data.crimes, data.vehicles, data.npcs = nil, nil, nil
            s = json.encode(data)
            if #s >= STATE_WIDTH then data.loot, data.bodies = nil, nil; s = json.encode(data) end
            if #s >= STATE_WIDTH then data.qh, data.enemies = nil, nil; s = json.encode(data) end
        end
        if s and #s < STATE_WIDTH then
            fh:seek('set', 0)
            fh:write(pad(s, STATE_WIDTH) .. '\n')
            fh:flush()
            lastExport = data
        end
    end
end)

-- =====================================================================================
-- CANAL DE COMMANDES (Python -> Lua) : cmd.json, lu toutes les 0,25 s.
--   {"seq":N,"cmd":"path_to_quest"}            chemin vers le marqueur de l objectif suivi
--   {"seq":N,"cmd":"path_to","x":..,"y":..,"z":..}   chemin vers un point
-- Reponse : path.json {"seq":N,"ok":..,"reason":..,"target":{x,y,z},"points":[[x,y,z],..],
--           "length":L,"partial":bool,"seqEnd":N}. seq repete en fin : lecture dechiree rejetee.
-- Navmesh streame par secteurs : si le chemin complet echoue, on vise un point
-- intermediaire (<= 60 m) vers la cible et on marque partial=true ; Python redemande.
-- =====================================================================================
local function writePath(resp)
    local f = io.open('path.json', 'w')
    if f then f:write(json.encode(resp)); f:flush(); f:close() end
end


local function questTarget()
    local jm = Game.GetJournalManager()
    local e = jm:GetTrackedEntry()
    if not e then return nil, 'aucune quete suivie' end
    local m = Game.GetMappinSystem():GetMappinFromObjective(jm:GetParentEntry(e), e)
    if not m then return nil, 'objectif sans marqueur' end
    return m:GetWorldPosition(), nil
end

local function computePath(player, target, avoid)
    local ai = Game.GetAINavigationSystem()
    local pos = player:GetWorldPosition()
    local a = ai:FindPointInSphereForCharacter(pos, 3.0, player)
    if not (a and a.point and tostring(a.status):find('OK')) then return nil, 'depart hors navmesh', false end
    local b = ai:FindPointInSphereForCharacter(target, 6.0, player)
    local partial = false
    local path = nil
    if b and b.point and tostring(b.status):find('OK') then
        path = ai:CalculatePathForCharacter(a.point, b.point, 2.0, player)
    end
    if not path then
        -- point intermediaire vers la cible : on ECHANTILLONNE (distances decroissantes,
        -- decalages lateraux) car en ville le point "tout droit" tombe souvent dans un mur.
        local dx, dy = target.x - pos.x, target.y - pos.y
        local d = math.sqrt(dx * dx + dy * dy)
        local ux, uy = dx / d, dy / d          -- direction
        local px, py = -uy, ux                 -- perpendiculaire
        local best, bestLen = nil, -1
        local tries = 0
        for _, step in ipairs({ 80, 60, 45, 30, 20 }) do
            if step < d - 5 then
                for _, lat in ipairs({ 0, 15, -15, 30, -30 }) do
                    tries = tries + 1
                    local mid = Vector4.new(pos.x + ux * step + px * lat, pos.y + uy * step + py * lat, pos.z, 1.0)
                    local c = ai:FindPointInSphereForCharacter(mid, 20.0, player)
                    local nearAvoid = false
                    if avoid and c and c.point then
                        nearAvoid = ((c.point.x - avoid.x) ^ 2 + (c.point.y - avoid.y) ^ 2) < 25 * 25
                    end
                    if c and c.point and tostring(c.status):find('OK') and not nearAvoid then
                        local cand = ai:CalculatePathForCharacter(a.point, c.point, 2.0, player)
                        if cand and avoid then
                            -- un chemin qui traverse la zone bloquante est ecarte
                            for k = 1, #cand.path do
                                local w = cand.path[k]
                                if ((w.x - avoid.x) ^ 2 + (w.y - avoid.y) ^ 2) < 6 * 6 then cand = nil; break end
                            end
                        end
                        if cand then
                            -- on prefere le chemin qui rapproche le plus de la cible
                            local e = cand.path[#cand.path]
                            local gain = d - math.sqrt((target.x - e.x) ^ 2 + (target.y - e.y) ^ 2)
                            if gain > bestLen then best, bestLen = cand, gain end
                        end
                    end
                end
                if best then break end
            end
        end
        journal(string.format('PATH partiel : %d essais, gain=%.0f m', tries, bestLen))
        if not best or bestLen < 5 then return nil, 'aucun chemin, meme partiel (' .. tries .. ' essais)', false end
        path, partial = best, true
    end
    return path, nil, partial
end

local lastDoors = {}                         -- entites porte/dispositif par index de la derniere liste `doors`
local lastFastTravel = {}                    -- points de voyage rapide par index
local lastContacts = {}                      -- contacts du telephone (sms_list)
local mappinProbeDone = false                -- sonde des methodes de mappin (une fois)
local lastChoices = {}                       -- choix de reponse SMS (sms_read)
local lastVendorStock = {}                   -- ItemID par index de la derniere liste de stock marchand
-- marchand PRESENT (< 6 m) : une entree par entite, priorite a IsVendor()
local function findNearbyVendor(player)
    -- le MARCHAND = parmi les PNJ a moins de 8 m, celui qui a le plus gros stock (chez le charcudoc, l infirmiere
    -- au comptoir etait prise pour le vendeur : 1 article, contre 409 pour le ripperdoc)
    local q = Game['TSQ_NPC;']()
    q.maxDistance = 8.0
    q.filterObjectByDistance = true
    pcall(function() q.testedSet = TargetingSet.Complete end)
    local okT, parts = Game.GetTargetingSystem():GetTargetParts(player, q)
    local vendor, bestCount = nil, -1
    local selfKey = nil
    pcall(function() selfKey = tostring(player:GetEntityID().hash) end)
    local ts = Game.GetTransactionSystem()
    if okT and parts then
        local seenEnt = {}
        for i = 1, #parts do
            local comp = TS_TargetPartInfo.GetComponent(parts[i])
            local ent = comp and comp:GetEntity() or nil
            if ent then
                local okH, h = pcall(function() return ent:GetEntityID().hash end)
                local key = okH and tostring(h) or tostring(ent)
                if seenEnt[key] or key == selfKey then ent = nil else seenEnt[key] = true end
            end
            if ent then
                local count = 0
                pcall(function()
                    local okL, items = ts:GetItemList(ent)
                    if type(okL) == 'table' then items = okL end
                    if type(items) == 'table' then count = #items end
                end)
                local isV = false
                pcall(function() isV = ent:IsVendor() end)
                if isV then count = count + 1000 end
                if count > bestCount then vendor, bestCount = ent, count end
            end
        end
    end
    return vendor
end
-- prix d achat d un objet chez ce marchand : RPGManager.CalculateBuyPrice (signatures variables), sinon Price x1
local function buyPrice(vendor, player, id)
    local price = nil
    local okA, a = pcall(function() return RPGManager.CalculateBuyPrice(vendor, player, id, 1.0) end)
    if okA and type(a) == 'number' and a > 0 then price = a end
    if not price then
        local okB, b = pcall(function() return RPGManager.CalculateBuyPrice(vendor, id) end)
        if okB and type(b) == 'number' and b > 0 then price = b end
    end
    if not price then
        pcall(function() price = math.floor(Game.GetTransactionSystem():GetItemData(vendor, id):GetStatValueByType(gamedataStatType.Price)) end)
    end
    if not price or price <= 0 then
        -- le stat Price est souvent nul : estimation par qualite (et x5 pour le cyberware), pour ne JAMAIS acheter gratuitement
        local q, t = 'Common', ''
        pcall(function() q = tostring(RPGManager.GetItemDataQuality(Game.GetTransactionSystem():GetItemData(vendor, id))):gsub('gamedataQuality : ', ''):gsub(' %(%d+%)', '') end)
        pcall(function() t = tostring(TweakDBInterface.GetItemRecord(ItemID.GetTDBID(id)):ItemType():Type()) end)
        local base = ({ Common = 300, Uncommon = 900, Rare = 2500, Epic = 7000, Legendary = 20000 })[q] or 600
        if t:find('Cyb') then base = base * 5 end
        price = base
    end
    return price or 0
end

local function handleCommand(player, cmd)
    local resp = { seq = cmd.seq, ok = false, seqEnd = cmd.seq }
    local target, err
    if cmd.cmd == 'inventory' then
        -- liste de l inventaire : nom, type, qualite, dps/armure, quantite, poids, iconique, equipe
        journal('RUN  inventory')
        local ts = Game.GetTransactionSystem()
        local es = Game.GetScriptableSystemsContainer():Get('EquipmentSystem')
        local edata = es:GetPlayerData(player)
        local okL, items = ts:GetItemList(player)
        if type(okL) == 'table' then items = okL end          -- selon la convention de retour
        lastInventory = {}
        local out = {}
        if type(items) == 'table' then
            for i = 1, #items do
                local data = items[i]
                local id = data:GetID()
                local rec = { i = i }
                lastInventory[i] = id
                pcall(function() rec.name = GetLocalizedTextByKey(TweakDBInterface.GetItemRecord(id.id):DisplayName()) end)
                pcall(function() rec.type = tostring(TweakDBInterface.GetItemRecord(id.id):ItemType():Type()):gsub('gamedataItemType : ', ''):gsub(' %(%d+%)', '') end)
                pcall(function() rec.quality = tostring(RPGManager.GetItemDataQuality(data)):gsub('gamedataQuality : ', ''):gsub(' %(%d+%)', '') end)
                pcall(function() rec.iconic = RPGManager.IsItemIconic(data) end)
                pcall(function() rec.qty = data:GetQuantity() end)
                pcall(function() rec.dps = data:GetStatValueByType(gamedataStatType.EffectiveDPS) end)
                pcall(function() rec.armor = data:GetStatValueByType(gamedataStatType.Armor) end)
                pcall(function() rec.weight = data:GetStatValueByType(gamedataStatType.Weight) end)
                pcall(function() rec.price = data:GetStatValueByType(gamedataStatType.Price) end)
                if not rec.price or rec.price <= 0 then
                    -- le stat Price est nul pour la plupart des objets : prix de vente calcule par le jeu (V comme « marchand »)
                    pcall(function() local p = RPGManager.CalculateSellPrice(player, player, id); if type(p) == 'number' and p > 0 then rec.price = p / 0.15 end end)
                    if not rec.price or rec.price <= 0 then pcall(function() local p = RPGManager.CalculateSellPrice(player, id); if type(p) == 'number' and p > 0 then rec.price = p / 0.15 end end) end
                end
                pcall(function() rec.equipped = edata:IsEquipped(id) end)
                pcall(function() rec.quest = data:HasTag('Quest') end)
                out[#out + 1] = rec
            end
        end
        resp.ok, resp.items = true, out
        -- emplacements d arme 1..3 : ce qui y est equipe, avec DPS (pour degainer le meilleur)
        pcall(function()
            local slots = {}
            for i = 0, 2 do
                local sid = edata:GetItemInEquipSlot(gamedataEquipmentArea.Weapon, i)
                local rec = { slot = i + 1 }
                if sid and ItemID.IsValid(sid) then
                    pcall(function() rec.name = GetLocalizedTextByKey(TweakDBInterface.GetItemRecord(sid.id):DisplayName()) end)
                    pcall(function() rec.type = tostring(TweakDBInterface.GetItemRecord(sid.id):ItemType():Type()):gsub('gamedataItemType : ', ''):gsub(' %(%d+%)', '') end)
                    pcall(function() rec.dps = ts:GetItemData(player, sid):GetStatValueByType(gamedataStatType.EffectiveDPS) end)
                end
                slots[#slots + 1] = rec
            end
            resp.slots = slots
        end)
        pcall(function()
            local worn = {}
            local wornIds = {}
            for _, area in ipairs({ 'Head', 'Face', 'OuterChest', 'InnerChest', 'Legs', 'Feet', 'Outfit' }) do
                local okA, wid = pcall(function() return edata:GetItemInEquipSlot(gamedataEquipmentArea[area], 0) end)
                if okA and wid and ItemID.IsValid(wid) then
                    local nm = nil
                    pcall(function() nm = GetLocalizedTextByKey(TweakDBInterface.GetItemRecord(ItemID.GetTDBID(wid)):DisplayName()) end)
                    worn[area] = nm or tostring(ItemID.GetTDBID(wid))
                    pcall(function() wornIds[tostring(ItemID.GetTDBID(wid))] = true end)
                end
            end
            resp.worn = worn
            for i = 1, #out do
                local rec = out[i]
                if rec.type and rec.type:sub(1, 4) == 'Clo_' and lastInventory[rec.i] then
                    local okT, tid = pcall(function() return tostring(ItemID.GetTDBID(lastInventory[rec.i])) end)
                    if okT and wornIds[tid] then rec.equipped = true end
                end
            end
        end)
        pcall(function() resp.money = ts:GetItemQuantity(player, MarketSystem.Money()) end)
        pcall(function() resp.weight = Game.GetStatsSystem():GetStatValue(player:GetEntityID(), gamedataStatType.Weight) end)
        pcall(function() resp.carry = Game.GetStatsSystem():GetStatValue(player:GetEntityID(), gamedataStatType.CarryCapacity) end)
        journal(string.format('OK   inventory : %d objets', #out))
        return resp
    elseif cmd.cmd == 'equip' then
        -- equipe l objet n (index de la derniere liste) dans l emplacement d arme y (0..2)
        local id = lastInventory[cmd.x or -1]
        if not id then resp.reason = 'index inconnu (refaire inventory)'; return resp end
        journal(string.format('RUN  equip idx=%d slot=%d', cmd.x, cmd.y or 0))
        local es = Game.GetScriptableSystemsContainer():Get('EquipmentSystem')
        local edata = es:GetPlayerData(player)
        local isClo = false
        pcall(function()
            local tt = tostring(TweakDBInterface.GetItemRecord(ItemID.GetTDBID(id)):ItemType():Type())
            isClo = tt:find('Clo_') ~= nil or tt:find('Cyb') ~= nil or tt:find('Cyberware') ~= nil
        end)
        local function isWorn()
            local w = false
            pcall(function() w = edata:IsEquipped(id) end)
            if not w then
                pcall(function()
                    local areas = { 'Head', 'Face', 'OuterChest', 'InnerChest', 'Legs', 'Feet', 'Outfit',
                                    'SystemReplacementCW', 'ArmsCW', 'LegsCW', 'HandsCW', 'EyesCW', 'MusculoskeletalSystemCW',
                                    'NervousSystemCW', 'CardiovascularSystemCW', 'ImmuneSystemCW', 'IntegumentarySystemCW', 'FrontalCortexCW' }
                    for _, area in ipairs(areas) do
                        for slot = 0, 3 do
                            local okW, wid = pcall(function() return edata:GetItemInEquipSlot(gamedataEquipmentArea[area], slot) end)
                            if okW and wid and ItemID.IsValid(wid) and tostring(ItemID.GetTDBID(wid)) == tostring(ItemID.GetTDBID(id)) then w = true end
                        end
                    end
                end)
            end
            return w
        end
        local used = 'EquipItem(id, slot)'
        pcall(function() edata:EquipItem(id, cmd.y or 0) end)
        if isClo and not isWorn() then
            used = 'EquipItem(id)'
            pcall(function() edata:EquipItem(id) end)
        end
        if isClo and not isWorn() then
            used = 'EquipItem(id, false, false, false)'
            pcall(function() edata:EquipItem(id, false, false, false) end)
        end
        resp.ok, resp.worn, resp.method = true, isWorn(), used
        journal(string.format('OK   equip : %s -> porte=%s', used, tostring(resp.worn)))
        return resp
    elseif cmd.cmd == 'disassemble' then
        local id = lastInventory[cmd.x or -1]
        if not id then resp.reason = 'index inconnu (refaire inventory)'; return resp end
        journal(string.format('RUN  disassemble idx=%d', cmd.x))
        ItemActionsHelper.DisassembleItem(player, id, cmd.y or 1)
        resp.ok = true
        journal('OK   disassemble')
        return resp
    elseif cmd.cmd == 'recipes' then
        journal('RUN  recipes')
        local cs = Game.GetScriptableSystemsContainer():Get('CraftingSystem')
        local cb = cs:GetPlayerCraftBook()
        local out = {}
        lastRecipes = {}
        local arr = nil
        pcall(function() arr = cb.knownRecipes end)
        if type(arr) == 'table' then
            for i = 1, #arr do
                local r = arr[i]
                local rec = { i = i, hidden = false, amount = 1 }
                pcall(function() rec.hidden = r.isHidden end)
                pcall(function() rec.amount = r.amount end)
                pcall(function() rec.tdbid = tostring(r.targetItem) end)
                pcall(function() rec.name = GetLocalizedTextByKey(TweakDBInterface.GetItemRecord(r.targetItem):DisplayName()) end)
                pcall(function() rec.type = tostring(TweakDBInterface.GetItemRecord(r.targetItem):ItemType():Type()):gsub('gamedataItemType : ', ''):gsub(' %(%d+%)', '') end)
                pcall(function() rec.can = cs:CanItemBeCrafted(TweakDBInterface.GetItemRecord(r.targetItem)) end)
                pcall(function() rec.quality = tostring(TweakDBInterface.GetItemRecord(r.targetItem):Quality():Type()):gsub('gamedataQuality : ', ''):gsub(' %(%d+%)', '') end)
                lastRecipes[i] = r.targetItem
                out[#out + 1] = rec
            end
        end
        resp.ok, resp.recipes = true, out
        if not craftDiagDone then
            craftDiagDone = true
            for i = 1, #out do
                if not out[i].hidden then
                    local tid = lastRecipes[out[i].i]
                    local okA, a = pcall(function() return cs:CanItemBeCrafted(player, TweakDBInterface.GetItemRecord(tid)) end)
                    local okB, b = pcall(function() return cs:CanItemBeCrafted(TweakDBInterface.GetItemRecord(tid)) end)
                    local okC, c = pcall(function() return cs:GetItemCraftingRecipe(TweakDBInterface.GetItemRecord(tid)) end)
                    journal(string.format('CRAFTDIAG %s : can(player,rec)=%s/%s can(rec)=%s/%s recipe=%s/%s', tostring(out[i].name), tostring(okA), tostring(a), tostring(okB), tostring(b), tostring(okC), tostring(c)))
                    break
                end
            end
        end
        journal('OK   recipes : ' .. #out)
        return resp
    elseif cmd.cmd == 'craft' then
        local tid = lastRecipes[cmd.x or -1]
        if not tid then resp.reason = 'index de recette inconnu (refaire recipes)'; return resp end
        journal(string.format('RUN  craft idx=%d qty=%d', cmd.x, cmd.y or 1))
        local cs = Game.GetScriptableSystemsContainer():Get('CraftingSystem')
        cs:CraftItem(player, TweakDBInterface.GetItemRecord(tid), cmd.y or 1)
        resp.ok = true
        journal('OK   craft')
        return resp
    elseif cmd.cmd == 'use' then
        -- consommer / utiliser un objet de l inventaire (nourriture, boisson, inhalateur...)
        local id = lastInventory[cmd.x or -1]
        if not id then resp.reason = 'index inconnu (refaire inventory)'; return resp end
        journal(string.format('RUN  use idx=%d', cmd.x))
        local okU = pcall(function() ItemActionsHelper.UseItem(player, id) end)
        if not okU then pcall(function() ItemActionsHelper.EatItem(player, id) end) end
        resp.ok = true
        journal('OK   use')
        return resp
    elseif cmd.cmd == 'levelup' then
        -- depenser les points d attribut / de perk selon un build MELEE (Corps, Reflexes, Sang-froid)
        journal('RUN  levelup')
        local okPds, pds = pcall(function() return Game.GetScriptableSystemsContainer():Get('PlayerDevelopmentSystem') end)
        if not okPds or not pds then resp.reason = 'PlayerDevelopmentSystem introuvable: ' .. tostring(pds); journal('FAIL levelup ' .. resp.reason); return resp end
        local okPdd, pdd = pcall(function() return pds:GetData(player) end)
        if not okPdd or not pdd then
            local ok2, pdd2 = pcall(function() return PlayerDevelopmentSystem.GetData(player) end)
            if ok2 and pdd2 then pdd = pdd2 else resp.reason = 'GetData: ' .. tostring(pdd); journal('FAIL levelup ' .. resp.reason); return resp end
        end
        local before, after = -1, -1
        pcall(function() before = pdd:GetDevPoints(gamedataDevelopmentPointType.Attribute) end)
        local order = { gamedataStatType.Strength, gamedataStatType.Reflexes, gamedataStatType.Cool, gamedataStatType.TechnicalAbility, gamedataStatType.Intelligence }
        local bought = 0
        if before and before > 0 then
            for round = 1, before do
                for _, stat in ipairs(order) do
                    local lvl = 0
                    pcall(function() lvl = Game.GetStatsSystem():GetStatValue(player:GetEntityID(), stat) end)
                    if lvl < 20 then
                        journal('RUN  levelup.BuyAttribute ' .. tostring(stat))
                        local okB = pcall(function() pdd:BuyAttribute(stat) end)
                        if okB then bought = bought + 1 end
                        break
                    end
                end
            end
        end
        pcall(function() after = pdd:GetDevPoints(gamedataDevelopmentPointType.Attribute) end)
        local perkPts = -1
        pcall(function() perkPts = pdd:GetDevPoints(gamedataDevelopmentPointType.Primary) end)
        resp.ok, resp.attribute_points_before, resp.attribute_points_after, resp.bought, resp.perk_points = true, before, after, bought, perkPts
        journal(string.format('OK   levelup : attributs %s -> %s (achetes %d), perks dispo %s', tostring(before), tostring(after), bought, tostring(perkPts)))
        return resp
    elseif cmd.cmd == 'sell' then
        -- VENTE : aucune fonction de marquage camelote n existe ; on realise la transaction
        -- que ferait l ecran du marchand : l objet part chez le marchand PRESENT (< 6 m), V recoit
        -- le prix de vente calcule par le jeu (RPGManager.CalculateSellPrice). Journalise.
        local id = lastInventory[cmd.x or -1]
        if not id then resp.reason = 'index inconnu (refaire inventory)'; return resp end
        local qtyWanted = math.floor(tonumber(cmd.y) or 1)
        journal(string.format('RUN  sell idx=%d qty=%d', cmd.x, qtyWanted))
        local vendor = findNearbyVendor(player)
        if not vendor then resp.reason = 'aucun marchand a portee'; journal('FAIL sell : aucun marchand'); return resp end
        local ts = Game.GetTransactionSystem()
        local owned = 0
        pcall(function() owned = ts:GetItemQuantity(player, id) end)
        if not owned or owned <= 0 then pcall(function() owned = ts:GetItemData(player, id):GetQuantity() end) end
        if not owned or owned <= 0 then resp.reason = 'objet absent de l inventaire'; journal('FAIL sell : absent'); return resp end
        local qty = math.max(1, math.min(qtyWanted, owned))
        -- objets proteges : quete, iconique, equipe
        local protected = false
        pcall(function()
            local d = ts:GetItemData(player, id)
            if d and d:HasTag(CName.new('Quest')) then protected = true end
            if RPGManager.IsItemIconic(d) then protected = true end
        end)
        pcall(function()
            if Game.GetScriptableSystemsContainer():Get('EquipmentSystem'):GetPlayerData(player):IsEquipped(id) then protected = true end
        end)
        if protected then resp.reason = 'objet protege (quete / iconique / equipe)'; journal('FAIL sell : protege'); return resp end
        local price = 0
        local okP, pr = pcall(function() return RPGManager.CalculateSellPrice(vendor, id) end)
        if okP and type(pr) == 'number' and pr > 0 then price = pr else
            pcall(function() price = math.floor(ts:GetItemData(player, id):GetStatValueByType(gamedataStatType.Price) * 0.15) end)
        end
        if not price or price <= 0 then
            price = math.floor((buyPrice(vendor, player, id) or 0) * 0.1)     -- estimation par qualite : jamais donne
        end
        local okX, moved = pcall(function() return ts:TransferItem(player, vendor, id, qty) end)
        if not okX or moved == false then
            resp.reason = 'TransferItem refuse (' .. tostring(moved) .. ')'; journal('FAIL sell : ' .. resp.reason); return resp
        end
        local total = math.floor(price * qty)
        local okM, paid = pcall(function() return ts:GiveItem(player, MarketSystem.Money(), total) end)
        if not okM or paid == false then journal('WARN sell : GiveItem(Money) a echoue : ' .. tostring(paid)) end
        resp.ok, resp.price, resp.total, resp.qty = true, price, total, qty
        journal(string.format('OK   sell : %d x -> %d eddies', qty, total))
        return resp
    elseif cmd.cmd == 'vendor_stock' then
        -- stock du marchand present : nom, type, quantite, prix d achat (pour acheter des soins etc.)
        journal('RUN  vendor_stock')
        local vendor = findNearbyVendor(player)
        if not vendor then resp.reason = 'aucun marchand a portee'; journal('FAIL vendor_stock : aucun marchand'); return resp end
        local ts = Game.GetTransactionSystem()
        local okL, items = ts:GetItemList(vendor)
        if type(okL) == 'table' then items = okL end
        lastVendorStock, lastVendorQty = {}, {}
        pcall(function() lastVendorKey = tostring(vendor:GetEntityID().hash) end)
        local out = {}
        if type(items) == 'table' then
            for i = 1, #items do
                local rec = { i = i }
                local idata = items[i]
                local id = nil
                pcall(function() id = idata:GetID() end)
                if id then
                    lastVendorStock[i] = id
                    pcall(function() rec.qty = idata:GetQuantity() end)
                    lastVendorQty[i] = tonumber(rec.qty) or 1
                    pcall(function() rec.name = GetLocalizedTextByKey(TweakDBInterface.GetItemRecord(ItemID.GetTDBID(id)):DisplayName()) end)
                    pcall(function() rec.type = tostring(TweakDBInterface.GetItemRecord(ItemID.GetTDBID(id)):ItemType():Type()):gsub('gamedataItemType : ', ''):gsub(' %(%d+%)', '') end)
                    pcall(function() rec.quality = tostring(RPGManager.GetItemDataQuality(idata)):gsub('gamedataQuality : ', ''):gsub(' %(%d+%)', '') end)
                    rec.price = buyPrice(vendor, player, id)
                    out[#out + 1] = rec
                end
            end
        end
        pcall(function() resp.money = ts:GetItemQuantity(player, MarketSystem.Money()) end)
        pcall(function() resp.vendor = GetLocalizedText(tostring(vendor:GetDisplayName())) end)
        resp.ok, resp.items = true, out
        journal(string.format('OK   vendor_stock : %d articles, %s eddies', #out, tostring(resp.money)))
        return resp
    elseif cmd.cmd == 'buy' then
        -- ACHAT : l article x (index de vendor_stock) en quantite y ; l objet passe du marchand a V,
        -- V paie le prix d achat calcule par le jeu. Refuse si pas assez d eddies.
        local id = lastVendorStock[cmd.x or -1]
        if not id then resp.reason = 'index inconnu (refaire vendor_stock)'; return resp end
        local vendor = findNearbyVendor(player)
        if not vendor then resp.reason = 'aucun marchand a portee'; return resp end
        local vkey = nil
        pcall(function() vkey = tostring(vendor:GetEntityID().hash) end)
        if lastVendorKey and vkey ~= lastVendorKey then resp.reason = 'marchand different de vendor_stock (refaire vendor_stock)'; journal('FAIL buy : ' .. resp.reason); return resp end
        local qty = math.floor(tonumber(cmd.y) or 1)
        local inStock = lastVendorQty[cmd.x or -1] or 1
        if qty < 1 then qty = 1 end
        if qty > inStock then qty = inStock end
        journal(string.format('RUN  buy idx=%d qty=%d', cmd.x, qty))
        local ts = Game.GetTransactionSystem()
        local price = buyPrice(vendor, player, id)
        if not price or price <= 0 then resp.reason = 'prix inconnu, achat refuse'; journal('FAIL buy : prix inconnu'); return resp end
        local total = math.floor(price * qty)
        local money = 0
        pcall(function() money = ts:GetItemQuantity(player, MarketSystem.Money()) end)
        if money < total then resp.reason = string.format('pas assez d eddies (%d < %d)', money, total); journal('FAIL buy : ' .. resp.reason); return resp end
        local okPay, paid = pcall(function() return ts:RemoveItem(player, MarketSystem.Money(), total) end)
        if not okPay or paid == false then resp.reason = 'paiement refuse (' .. tostring(paid) .. ')'; journal('FAIL buy : ' .. resp.reason); return resp end
        local okX, moved = pcall(function() return ts:TransferItem(vendor, player, id, qty) end)
        if not okX or moved == false then
            pcall(function() ts:GiveItem(player, MarketSystem.Money(), total) end)     -- rembourse
            resp.reason = 'transfert refuse (' .. tostring(moved) .. '), rembourse'; journal('FAIL buy : ' .. resp.reason); return resp
        end
        resp.ok, resp.price, resp.total, resp.qty = true, price, total, qty
        journal(string.format('OK   buy : %d x -> %d eddies', qty, total))
        return resp
    elseif cmd.cmd == 'loot' then
        -- LOOT PAR SCRIPT : l interface de loot (tooltip + E) est capricieuse ; on transfere directement
        -- le contenu de l objet vise (conteneur, sac, objet au sol, corps) vers V, comme le ferait
        -- « tout prendre ». Cible = entite lootable la plus proche du point (x,y) a < 2 m, a < 6 m de V.
        journal(string.format('RUN  loot (%.1f,%.1f)', cmd.x or 0, cmd.y or 0))
        local q = Game['TSQ_ALL;']()
        q.maxDistance = 6.0
        q.filterObjectByDistance = true
        pcall(function() q.testedSet = TargetingSet.Complete end)
        local okT, parts = Game.GetTargetingSystem():GetTargetParts(player, q)
        local best, bestD, bestCls = nil, 3.2, '?'        -- 3,2 m autour du point vise (les positions derivent, V est souvent bloque a 2-3 m)
        if okT and parts then
            local seenEnt = {}
            for i = 1, #parts do
                local comp = TS_TargetPartInfo.GetComponent(parts[i])
                local ent = comp and comp:GetEntity() or nil
                if ent then
                    local okH, h = pcall(function() return ent:GetEntityID().hash end)
                    local key = okH and tostring(h) or tostring(ent)
                    if not seenEnt[key] then
                        seenEnt[key] = true
                        local cls = nil
                        for _, cn in ipairs({ 'gameLootContainerBase', 'gameItemDropObject', 'gameLootBag', 'gameContainerObject', 'gamePuppet' }) do
                            local okI, isIt = pcall(function() return ent:IsA(cn) end)
                            if okI and isIt then cls = cn; break end
                        end
                        if not cls then
                            local okDead2, dead2 = pcall(function() return ent:IsDead() end)
                            if okDead2 and dead2 then cls = 'gamePuppet' end     -- PNJ mort d une autre classe : on le fouille aussi
                        end
                        if cls then
                            local okDead, dead = pcall(function() return ent:IsDead() end)
                            if cls ~= 'gamePuppet' or (okDead and dead) then
                                local wp = ent:GetWorldPosition()
                                local dd = math.sqrt((wp.x - (cmd.x or wp.x)) ^ 2 + (wp.y - (cmd.y or wp.y)) ^ 2)
                                if dd < bestD then best, bestD, bestCls = ent, dd, cls end
                            end
                        end
                    end
                end
            end
        end
        if not best then resp.reason = 'aucun objet lootable a cet endroit'; journal('FAIL loot : rien a (' .. tostring(cmd.x) .. ',' .. tostring(cmd.y) .. ')'); return resp end
        local ts = Game.GetTransactionSystem()
        local moved, names, methods, listed = 0, {}, {}, 0
        if bestCls == 'gameItemDropObject' then
            -- objet au sol : un seul ItemObject
            local id = nil
            pcall(function() id = best:GetItemObject():GetItemID() end)
            if id then
                local okX, didX = pcall(function() return ts:TransferItem(best, player, id, 1) end)
                if okX and didX ~= false then moved = 1; methods[#methods + 1] = 'TransferItem(drop)' end
                pcall(function() names[#names + 1] = GetLocalizedTextByKey(TweakDBInterface.GetItemRecord(ItemID.GetTDBID(id)):DisplayName()) end)
            end
        else
            local okL, items = ts:GetItemList(best)
            if type(okL) == 'table' then items = okL end
            if type(items) == 'table' then
                listed = #items
                for i = 1, #items do
                    local idata = items[i]
                    local id, qty = nil, 1
                    pcall(function() id = idata:GetID() end)
                    pcall(function() qty = idata:GetQuantity() end)
                    if id then
                        -- pas l argent « interne » ni les objets de quete (le jeu les donne lui-meme)
                        local skip = false
                        pcall(function() skip = idata:HasTag('Quest') or idata:HasTag('SkipActivityLog') end)
                        if not skip then
                            local okX = pcall(function() ts:TransferItem(best, player, id, qty or 1) end)
                            if okX then
                                moved = moved + 1; methods[#methods + 1] = 'TransferItem'
                                pcall(function() names[#names + 1] = GetLocalizedTextByKey(TweakDBInterface.GetItemRecord(ItemID.GetTDBID(id)):DisplayName()) end)
                            end
                        end
                    end
                end
            else
                -- pas de liste lisible : tentative « tout prendre » par l API du conteneur
                local okA = pcall(function() best:LootAll() end)
                if okA then moved = -1; methods[#methods + 1] = 'LootAll' end
            end
        end
        resp.ok, resp.cls, resp.transferes, resp.noms, resp.methodes, resp.total = true, bestCls, moved, names, methods, listed
        journal(string.format('OK   loot : %s (%.1f m du point) -> %d/%d objet(s) [%s] %s', bestCls, bestD, moved, listed, table.concat(methods, ','), table.concat(names, ' | '):sub(1, 160)))
        return resp
    elseif cmd.cmd == 'sms_list' then
        -- SMS : contacts du telephone avec messages non lus et options de reponse (JournalManager.GetContactDataArray)
        journal('RUN  sms_list')
        local jm = Game.GetJournalManager()
        local okD, a, b = pcall(function() return jm:GetContactDataArray(true, true) end)
        local data = nil
        if okD then
            if type(a) == 'table' then data = a elseif type(b) == 'table' then data = b end
        end
        if not data then resp.reason = 'GetContactDataArray : ' .. tostring(okD) .. '/' .. tostring(a) .. '/' .. tostring(b); journal('FAIL sms_list : ' .. resp.reason); return resp end
        local out = {}
        lastContacts = {}
        for i = 1, #data do
            local c = data[i]
            local rec = { i = i }
            pcall(function() rec.name = GetLocalizedText(tostring(c.localizedName)) end)
            pcall(function()
                local u = c.unreadMessages
                if type(u) == 'table' then u = #u end
                rec.unread = tonumber(u) or 0
            end)
            pcall(function()
                local n = c.messagesCount
                if type(n) == 'table' then n = #n end
                rec.count = tonumber(n) or 0
            end)
            if i == 1 then
                -- sonde (une fois) : champs disponibles sur ContactData
                for _, fn in ipairs({ 'contactId', 'localizedName', 'unreadMessages', 'messagesCount', 'playerCanReply', 'hasMessages', 'lastMesssagePreview', 'activeReplyOptions', 'contactEntry', 'id', 'timeStamp', 'isCallable' }) do
                    local okF, v = pcall(function() return c[fn] end)
                    journal(string.format('OK   contact[1].%s -> %s / %s (%s)', fn, tostring(okF), tostring(v), type(v)))
                end
            end
            pcall(function() rec.can_reply = c.playerCanReply end)
            pcall(function() rec.preview = GetLocalizedText(tostring(c.lastMesssagePreview)) end)
            pcall(function() rec.has_replies = (type(c.activeReplyOptions) == 'table') and #c.activeReplyOptions or nil end)
            lastContacts[i] = c
            out[#out + 1] = rec
        end
        resp.ok, resp.contacts = true, out
        local nUnread = 0
        for _, r in ipairs(out) do nUnread = nUnread + (tonumber(r.unread) or 0) end
        journal(string.format('OK   sms_list : %d contacts, %d non lus', #out, nUnread))
        return resp
    elseif cmd.cmd == 'sms_read' then
        -- messages et choix de reponse d un contact (index de sms_list) : GetMessagesAndChoices(contactEntry, filter)
        local c = lastContacts[cmd.x or -1]
        if not c then resp.reason = 'index inconnu (refaire sms_list)'; return resp end
        journal(string.format('RUN  sms_read idx=%d', cmd.x))
        local jm = Game.GetJournalManager()
        local entry = nil
        pcall(function() entry = c.contactEntry end)
        -- c.id est l identifiant du contact dans le journal (ex. « coach », « viktor ») : chemin « contacts/<id> »
        local tried = {}
        -- GetEntryByString(path, context) : 2 parametres (sonde du 13/09 14:43) ; contextes essayes : '' puis 'contacts'
        for _, path in ipairs({ 'contacts/' .. tostring(c.id), tostring(c.id) }) do
            for _, ctx in ipairs({ '', 'contacts', 'gameJournalContact' }) do
                if entry then break end
                local okE, e = pcall(function() return jm:GetEntryByString(path, ctx) end)
                tried[#tried + 1] = path .. '[' .. ctx .. ']=' .. tostring(okE and e ~= nil) .. (okE and '' or (' err:' .. tostring(e)))
                if okE and e then entry = e end
            end
            if entry then break end
        end
        if not entry then
            resp.reason = 'entree de contact introuvable (' .. table.concat(tried, ' ; ') .. ')'
            journal('FAIL sms_read : ' .. resp.reason); return resp
        end
        -- les messages vivent dans les CONVERSATIONS du contact : contact -> GetConversations -> GetMessagesAndChoices(conv)
        local targets = {}
        local okC, c1, c2 = pcall(function() return jm:GetConversations(entry) end)
        local convs = (okC and type(c1) == 'table') and c1 or ((okC and type(c2) == 'table') and c2 or nil)
        if convs and #convs > 0 then
            for k = 1, #convs do targets[#targets + 1] = convs[k] end
        else
            targets[1] = entry
        end
        journal(string.format('OK   sms_read : GetConversations -> %s, %d conversation(s)', tostring(okC), convs and #convs or 0))
        local msgs, choices = {}, {}
        for _, tgt in ipairs(targets) do
        local okM, r1, r2, r3 = pcall(function() return jm:GetMessagesAndChoices(tgt, JournalRequestStateFilter.Any) end)
        if not okM then
            okM, r1, r2, r3 = pcall(function() return jm:GetMessagesAndChoices(tgt) end)
        end
        journal(string.format('OK   sms_read : GetMessagesAndChoices -> %s / %s(%s) / %s(%s) / %s', tostring(okM), type(r1), type(r1) == 'table' and #r1 or '-', type(r2), type(r2) == 'table' and #r2 or '-', type(r3)))
        local function textOf(e)
            local t = nil
            pcall(function() t = GetLocalizedText(tostring(e:GetText())) end)
            if not t then pcall(function() t = tostring(e:GetText()) end) end
            return t or '?'
        end
        for _, arr in ipairs({ r1, r2, r3 }) do
            if type(arr) == 'table' then
                for k = 1, #arr do
                    local e = arr[k]
                    local cls = ''
                    pcall(function() cls = tostring(e:GetClassName()) end)
                    if cls:find('Choice') then
                        choices[#choices + 1] = { i = #choices + 1, text = textOf(e) }
                        lastChoices[#choices] = e
                    else
                        local sender = nil
                        pcall(function() sender = e:IsPlayerSender() end)
                        msgs[#msgs + 1] = { text = textOf(e), from_v = sender }
                    end
                end
            end
        end
        end
        resp.ok, resp.messages, resp.choices = true, msgs, choices
        journal(string.format('OK   sms_read : %d messages, %d choix', #msgs, #choices))
        return resp
    elseif cmd.cmd == 'sms_reply' then
        -- repondre : activer l entree de choix (ce que fait l interface du telephone)
        local e = lastChoices[cmd.x or -1]
        if not e then resp.reason = 'choix inconnu (refaire sms_read)'; return resp end
        journal(string.format('RUN  sms_reply idx=%d', cmd.x))
        local jm = Game.GetJournalManager()
        local okC = pcall(function() jm:ChangeEntryState(e, gameJournalEntryState.Active, JournalNotifyOption.Notify) end)
        if not okC then okC = pcall(function() jm:ChangeEntryState(e, gameJournalEntryState.Active, JournalNotifyOption.DoNotNotify) end) end
        resp.ok = okC
        if not okC then resp.reason = 'ChangeEntryState refuse' end
        journal('OK   sms_reply : ' .. tostring(okC))
        return resp
    elseif cmd.cmd == 'fast_travel_points' then
        -- POINTS DE VOYAGE RAPIDE connus (FastTravelSystem est un ScriptableSystem, pas un membre de GameInstance)
        journal('RUN  fast_travel_points')
        local okS, fts = pcall(function() return Game.GetScriptableSystemsContainer():Get('FastTravelSystem') end)
        if not okS or not fts then resp.reason = 'FastTravelSystem indisponible'; journal('FAIL fast_travel_points'); return resp end
        local okP, pts = pcall(function() return fts:GetFastTravelPoints() end)
        if not okP or type(pts) ~= 'table' then resp.reason = 'GetFastTravelPoints : ' .. tostring(pts); journal('FAIL fast_travel_points : ' .. resp.reason); return resp end
        local pos = player:GetWorldPosition()
        local out = {}
        lastFastTravel = {}
        local ftMappins = {}
        pcall(function()
            local mps = Game.GetMappinSystem():GetMappins(gamemappinsMappinTargetType.Minimap)
            for k = 1, #mps do
                local v = ''
                pcall(function() v = tostring(mps[k]:GetVariant()) end)
                if v:find('FastTravel') or v:find('Metro') then
                    local w = mps[k]:GetWorldPosition()
                    ftMappins[#ftMappins + 1] = { x = w.x, y = w.y, z = w.z, d = math.sqrt((w.x - pos.x) ^ 2 + (w.y - pos.y) ^ 2), variant = v:gsub('gamedataMappinVariant : ', ''):gsub(' %(%d+%)', '') }
                end
            end
        end)
        resp.ft_mappins = ftMappins
        journal('OK   fast_travel_points : ' .. #ftMappins .. ' mappins de voyage rapide / metro')
        for i = 1, #pts do
            local p = pts[i]
            local rec = { i = i }
            if i <= 0 then
                -- sondes (desactivees : resultats connus, voir journal du 13/09 12:12)
                for _, mn in ipairs({ 'GetPointDisplayName', 'GetDistrictDisplayName', 'GetMarkerPosition', 'GetMappinID', 'GetPointRecord', 'GetMarkerRef', 'IsEnabled', 'GetTrackingType' }) do
                    local okM, v = pcall(function() return p[mn](p) end)
                    journal(string.format('OK   ftpoint[%d].%s -> %s / %s', i, mn, tostring(okM), tostring(v)))
                end
                for _, fn in ipairs({ 'pointRecord', 'markerRef', 'districtRecord', 'mappinID', 'position', 'mappinPos' }) do
                    local okF, v = pcall(function() return p[fn] end)
                    if okF and v ~= nil then
                        local sv = tostring(v)
                        pcall(function() sv = TDBID.ToStringDEBUG(v) or sv end)
                        journal(string.format('OK   ftpoint[%d].%s = %s', i, fn, sv))
                    end
                end
            end
            pcall(function() rec.name = GetLocalizedTextByKey(p:GetPointDisplayName()) end)
            if not rec.name or rec.name == '' then pcall(function() rec.name = GetLocalizedText(tostring(p:GetPointDisplayName())) end) end
            pcall(function() rec.district = GetLocalizedTextByKey(p:GetDistrictDisplayName()) end)
            if rec.district and rec.district:find('LocKey') then pcall(function() rec.district = GetLocalizedText(tostring(p:GetDistrictDisplayName())) end) end
            pcall(function() rec.record = TDBID.ToStringDEBUG(p.pointRecord) end)
            -- POSITION : via le mappin du point (champ mappinID, sonde du 13/09)
            pcall(function()
                local mp = Game.GetMappinSystem():GetMappin(p.mappinID)
                if mp then
                    local w = mp:GetWorldPosition()
                    if w and (w.x ~= 0 or w.y ~= 0) then rec.x, rec.y, rec.z = w.x, w.y, w.z; rec.d = math.sqrt((w.x - pos.x) ^ 2 + (w.y - pos.y) ^ 2) end
                end
            end)
            if not rec.x then
                -- repli : resolution du NodeRef du marqueur
                pcall(function()
                    local ent = Game.FindEntityByID(Game.GetEntityIDFromNodeRef(p.markerRef))
                    if ent then local w = ent:GetWorldPosition(); rec.x, rec.y, rec.z = w.x, w.y, w.z; rec.d = math.sqrt((w.x - pos.x) ^ 2 + (w.y - pos.y) ^ 2) end
                end)
            end
            pcall(function() rec.district = tostring(p:GetDistrictDisplayName()) end)
            pcall(function()
                local w = p:GetMarkerPosition()
                if w then rec.x, rec.y, rec.z = w.x, w.y, w.z; rec.d = math.sqrt((w.x - pos.x) ^ 2 + (w.y - pos.y) ^ 2) end
            end)
            lastFastTravel[i] = p
            out[#out + 1] = rec
            if rec.x and rec.y then lastFtPoints[#lastFtPoints + 1] = { x = rec.x, y = rec.y } end
        end
        local enabled = nil
        pcall(function() enabled = fts:IsFastTravelEnabled() end)
        resp.ok, resp.points, resp.enabled = true, out, enabled
        journal(string.format('OK   fast_travel_points : %d points, enabled=%s', #out, tostring(enabled)))
        return resp
    elseif cmd.cmd == 'fast_travel' then
        -- VOYAGE RAPIDE vers le point x (index de fast_travel_points) ; methodes essayees en pcall
        local p = lastFastTravel[cmd.x or -1]
        if not p then resp.reason = 'index inconnu (refaire fast_travel_points)'; return resp end
        journal(string.format('RUN  fast_travel idx=%d', cmd.x))
        local fts = Game.GetScriptableSystemsContainer():Get('FastTravelSystem')
        local did, errs = {}, {}
        local tries = {
            { 'PerformFastTravel(p, player)', function() fts:PerformFastTravel(p, player) end },
            { 'PerformFastTravel(p)', function() fts:PerformFastTravel(p) end },
            { 'FastTravel(p)', function() fts:FastTravel(p) end },
            { 'PerformFastTravel(record, player)', function() fts:PerformFastTravel(p.pointRecord, player) end },
        }
        for _, t in ipairs(tries) do
            local okX, err = pcall(t[2])
            if okX then did[#did + 1] = t[1]; break else errs[#errs + 1] = t[1] .. ' -> ' .. tostring(err) end
        end
        resp.ok, resp.methodes, resp.erreurs = (#did > 0), did, errs
        if #did == 0 then resp.reason = 'aucune methode de voyage acceptee' end
        journal('OK   fast_travel : [' .. table.concat(did, ',') .. '] erreurs: ' .. table.concat(errs, ' | '):sub(1, 400))
        return resp
    elseif cmd.cmd == 'bd_jump' then
        -- DANSE SENSORIELLE : saut de la timeline a x secondes (puis pause) ; z = vitesse du saut (defaut 10)
        local si = bdSceneIface()
        if not si then resp.reason = 'SceneSystem indisponible'; return resp end
        journal(string.format('RUN  bd_jump %.1f', tonumber(cmd.x) or 0))
        local okJ, r = pcall(function()
            return si:JumpRewindableSection(tonumber(cmd.z) or 10.0, tonumber(cmd.x) or 0.0, bdEnum('scnPlayDirection', 'Forward', 0), bdEnum('scnPlaySpeed', 'Pause', 0))
        end)
        resp.ok = okJ and (r ~= false)
        if not okJ then resp.reason = 'JumpRewindableSection : ' .. tostring(r) end
        pcall(function() resp.t = si:GetRewindableSectionTimeInSec() end)
        journal('OK   bd_jump : ' .. tostring(resp.ok) .. ' t=' .. tostring(resp.t) .. (okJ and '' or (' ' .. tostring(r))))
        return resp
    elseif cmd.cmd == 'bd_speed' then
        -- x = 0 pause, 1 lent, 2 normal, 3 rapide, 4 tres rapide ; y = 1 -> marche arriere
        local si = bdSceneIface()
        if not si then resp.reason = 'SceneSystem indisponible'; return resp end
        local names = { [0] = 'Pause', 'Slow', 'Normal', 'Fast', 'VeryFast' }
        local sp = math.max(0, math.min(4, math.floor(tonumber(cmd.x) or 0)))
        journal(string.format('RUN  bd_speed %d dir=%s', sp, tostring(cmd.y)))
        local okD = pcall(function() si:SetRewindableSectionPlayDirection(bdEnum('scnPlayDirection', (cmd.y == 1) and 'Backward' or 'Forward', (cmd.y == 1) and 1 or 0)) end)
        local okS, errS = pcall(function() si:SetRewindableSectionPlaySpeed(bdEnum('scnPlaySpeed', names[sp], sp)) end)
        resp.ok = okS
        if not okS then resp.reason = tostring(errS) end
        journal('OK   bd_speed : ' .. tostring(okS) .. ' dir=' .. tostring(okD))
        return resp
    elseif cmd.cmd == 'bd_clues' then
        -- diagnostic complet dans le journal : etat, indices de la timeline, entites-indices
        local bd = bdState()
        if not bd then resp.reason = 'pas de danse sensorielle active'; return resp end
        local pos = player:GetWorldPosition()
        local okF, focus = pcall(bdScanFocus, player, pos)
        bd.focus = okF and focus or nil
        journal(string.format('BD etat : actif=%s rew=%s fpp=%s couche=%s t=%s/%s pause=%s exit=%s masques=%s', tostring(bd.active), tostring(bd.rew),
            tostring(bd.fpp), tostring(bd.layer), tostring(bd.t), tostring(bd.dur), tostring(bd.paused), tostring(bd.exit), bd.masks and json.encode(bd.masks) or '?'))
        for _, c in ipairs(bd.clues or {}) do journal(string.format('BD timeline : %s %.1f-%.1f couche=%s mode=%s done=%s', c.name, c.t0 or -1, c.t1 or -1, tostring(c.layer), tostring(c.mode), tostring(c.done))) end
        for _, f in ipairs(bd.focus or {}) do
            journal(string.format('BD indice : %s [%s] d=%.1f couche=%s scanne=%s bloque=%s actif=%s prog=%s besoin=%s sx=%s sy=%s', tostring(f.name), tostring(f.cls), f.d or -1,
                tostring(f.layer), tostring(f.scanned), tostring(f.blocked), tostring(f.enabled), tostring(f.prog), tostring(f.need), tostring(f.sx), tostring(f.sy)))
        end
        if not okF then journal('BD indices : erreur ' .. tostring(focus)) end
        resp.ok, resp.bd = true, bd
        return resp
    elseif cmd.cmd == 'breach_info' then
        -- BREACH PROTOCOL : grille / sequences / buffer / chaines ; x = 1 -> journalise aussi tous les textes (diagnostic)
        local info = breachInfo(cmd)
        for k, v in pairs(info) do resp[k] = v end
        if info.ok then journal(string.format('OK   breach_info : etat %s, %d textes hex, %d chaines, grille %s, buffer %s',
            tostring(info.state), #(info.texts or {}), #(info.chains or {}), tostring(info.size), tostring(info.buffer)))
        else journal('FAIL breach_info : ' .. tostring(info.reason)) end
        return resp
    elseif cmd.cmd == 'teleport' then
        -- TELEPORTATION (TeleportationFacility) : reservee au voyage rapide borne -> borne quand l API du jeu refuse
        journal(string.format('RUN  teleport (%.0f,%.0f,%.0f)', cmd.x or 0, cmd.y or 0, cmd.z or 0))
        local function nearFt(x, y)
            for _, m in ipairs(lastFtPoints) do if (m.x - x) ^ 2 + (m.y - y) ^ 2 < 144 then return true end end
            return false
        end
        local herePos = player:GetWorldPosition()
        if not (cmd.x and cmd.y) or #lastFtPoints == 0 or not nearFt(cmd.x, cmd.y) or not nearFt(herePos.x, herePos.y) then
            resp.reason = 'teleport refuse : uniquement borne -> borne (refaire fast_travel_points, V a < 12 m d une borne)'
            journal('FAIL teleport : ' .. resp.reason); return resp
        end
        local okT, err = pcall(function()
            Game.GetTeleportationFacility():Teleport(player, Vector4.new(cmd.x, cmd.y, (cmd.z or player:GetWorldPosition().z) + 0.5, 1.0), EulerAngles.new(0, 0, 0))
        end)
        resp.ok = okT
        if not okT then resp.reason = 'Teleport : ' .. tostring(err) end
        journal('OK   teleport : ' .. tostring(okT) .. (okT and '' or (' ' .. tostring(err))))
        return resp
    elseif cmd.cmd == 'vehicle_call' then
        -- APPEL D UN VEHICULE AU HASARD parmi ceux que V possede (VehicleSystem) : x = 0 hasard, 1 voiture, 2 moto.
        journal('RUN  vehicle_call ' .. tostring(cmd.x))
        local vs = Game.GetVehicleSystem()
        if not vs then resp.reason = 'VehicleSystem indisponible'; return resp end
        local okL, list = pcall(function() return vs:GetPlayerUnlockedVehicles() end)
        if not okL or type(list) ~= 'table' or #list == 0 then
            resp.reason = 'aucun vehicule debloque lisible (' .. tostring(list) .. ')'; journal('FAIL vehicle_call : ' .. resp.reason); return resp
        end
        local cands = {}
        for i = 1, #list do
            local v = list[i]
            local rec, name, vtype = nil, '?', '?'
            pcall(function() rec = TweakDBInterface.GetVehicleRecord(v.recordID) end)
            if rec then
                pcall(function() name = GetLocalizedTextByKey(rec:DisplayName()) end)
                pcall(function() vtype = tostring(rec:Type():Type()):gsub('gamedataVehicleType : ', ''):gsub(' %(%d+%)', '') end)
            end
            local isBike = vtype:find('Bike') ~= nil
            if cmd.x == 0 or (cmd.x == 1 and not isBike) or (cmd.x == 2 and isBike) then
                cands[#cands + 1] = { v = v, name = name, vtype = vtype, isBike = isBike }
            end
        end
        if #cands == 0 then resp.reason = 'aucun vehicule de ce type'; journal('FAIL vehicle_call : ' .. resp.reason); return resp end
        local pick = cands[math.random(#cands)]
        local typeEnum = pick.isBike and gamedataVehicleType.Bike or gamedataVehicleType.Car
        local did = {}
        if pcall(function() vs:TogglePlayerActiveVehicle(pick.v, typeEnum, true) end) then did[#did + 1] = 'TogglePlayerActiveVehicle' end
        if pcall(function() vs:SpawnPlayerVehicle(typeEnum) end) then did[#did + 1] = 'SpawnPlayerVehicle' end
        resp.ok, resp.name, resp.vtype, resp.methodes, resp.total = (#did > 0), pick.name, pick.vtype, did, #list
        journal(string.format('OK   vehicle_call : %s (%s) parmi %d [%s]', tostring(pick.name), tostring(pick.vtype), #list, table.concat(did, ',')))
        return resp
    elseif cmd.cmd == 'perks' then
        -- DEPENSE DES POINTS DE PERK : jalons des arbres Corps / Reflexes / Sang-froid / Technique (build melee),
        -- essayes dans l ordre jusqu a epuisement des points ; chaque achat journalise.
        journal('RUN  perks')
        local okPds, pds = pcall(function() return Game.GetScriptableSystemsContainer():Get('PlayerDevelopmentSystem') end)
        if not okPds or not pds then resp.reason = 'PlayerDevelopmentSystem introuvable'; return resp end
        local pdd = nil
        pcall(function() pdd = pds:GetData(player) end)
        if not pdd then pcall(function() pdd = PlayerDevelopmentSystem.GetData(player) end) end
        if not pdd then resp.reason = 'GetData indisponible'; return resp end
        local pts = 0
        pcall(function() pts = pdd:GetDevPoints(gamedataDevelopmentPointType.Primary) end)
        local order = { 'Body_Central_Milestone_1', 'Reflexes_Central_Milestone_1', 'Cool_Central_Milestone_1',
                        'Body_Left_Milestone_1', 'Body_Right_Milestone_1', 'Reflexes_Left_Milestone_1', 'Reflexes_Right_Milestone_1',
                        'Body_Central_Milestone_2', 'Reflexes_Central_Milestone_2', 'Cool_Central_Milestone_2',
                        'Tech_Central_Milestone_1', 'Intelligence_Central_Milestone_1',
                        'Body_Central_Milestone_3', 'Reflexes_Central_Milestone_3', 'Cool_Central_Milestone_3',
                        'Body_Left_Milestone_2', 'Body_Right_Milestone_2', 'Reflexes_Left_Milestone_2', 'Reflexes_Right_Milestone_2' }
        local bought = {}
        local guard = 0
        while pts > 0 and guard < 12 do
            guard = guard + 1
            local progressed = false
            for _, nm in ipairs(order) do
                local okB, res = pcall(function() return pdd:BuyNewPerk(gamedataNewPerkType[nm]) end)
                if okB and res == true then
                    bought[#bought + 1] = nm; progressed = true
                    pcall(function() pts = pdd:GetDevPoints(gamedataDevelopmentPointType.Primary) end)
                    break
                end
            end
            if not progressed then break end
        end
        pcall(function() pts = pdd:GetDevPoints(gamedataDevelopmentPointType.Primary) end)
        resp.ok, resp.achetes, resp.restants = true, bought, pts
        journal(string.format('OK   perks : %d achete(s) [%s], restants %s', #bought, table.concat(bought, ','), tostring(pts)))
        return resp
    elseif cmd.cmd == 'radio' then
        -- RADIO (radioport) : x = 1 allumer, 0 eteindre, 2 station suivante. API PocketRadio (2.x), en pcall + journal.
        journal('RUN  radio ' .. tostring(cmd.x))
        local okR, pr = pcall(function() return player:GetPocketRadio() end)
        if not okR or not pr then resp.reason = 'PocketRadio indisponible : ' .. tostring(pr); journal('FAIL radio : ' .. resp.reason); return resp end
        local did = {}
        if cmd.x == 1 then
            if pcall(function() pr:TurnOn(false) end) then did[#did + 1] = 'TurnOn' else
                if pcall(function() pr:TurnOn() end) then did[#did + 1] = 'TurnOn()' end
            end
        elseif cmd.x == 0 then
            if pcall(function() pr:TurnOff() end) then did[#did + 1] = 'TurnOff' end
        elseif cmd.x == 2 then
            if pcall(function() pr:NextStation() end) then did[#did + 1] = 'NextStation' end
        end
        local station = nil
        pcall(function() station = GetLocalizedText(tostring(pr:GetStationName())) end)
        if not station or station == '' then pcall(function() station = tostring(pr:GetStationName()) end) end
        if not station or station == '' then pcall(function() station = 'station ' .. tostring(pr:GetStationIndex()) end) end
        local active = nil
        pcall(function() active = pr:IsActive() end)
        resp.ok, resp.station, resp.active, resp.methodes = (#did > 0), station, active, did
        if #did == 0 then resp.reason = 'aucune methode radio acceptee' end
        journal(string.format('OK   radio : [%s] station=%s active=%s', table.concat(did, ','), tostring(station), tostring(active)))
        return resp
    elseif cmd.cmd == 'doors' then
        -- PORTES / DISPOSITIFS proches (< 15 m) : pour sortir d un ilot de maillage ferme (piece, local)
        journal('RUN  doors')
        local q = Game['TSQ_ALL;']()
        q.maxDistance = 15.0
        q.filterObjectByDistance = true
        pcall(function() q.testedSet = TargetingSet.Complete end)
        local okT, parts = Game.GetTargetingSystem():GetTargetParts(player, q)
        local pos = player:GetWorldPosition()
        local out = {}
        lastDoors = {}
        if okT and parts then
            local seenEnt = {}
            for i = 1, #parts do
                local comp = TS_TargetPartInfo.GetComponent(parts[i])
                local ent = comp and comp:GetEntity() or nil
                if ent then
                    local okH, h = pcall(function() return ent:GetEntityID().hash end)
                    local key = okH and tostring(h) or tostring(ent)
                    if not seenEnt[key] then
                        seenEnt[key] = true
                        local kind = nil
                        for _, cn in ipairs({ 'Door', 'gameDevice' }) do
                            local okI, isIt = pcall(function() return ent:IsA(cn) end)
                            if okI and isIt then kind = cn; break end
                        end
                        if kind then
                            local wp = ent:GetWorldPosition()
                            local rec = { i = #out + 1, kind = kind, x = wp.x, y = wp.y, z = wp.z, d = math.sqrt((wp.x - pos.x) ^ 2 + (wp.y - pos.y) ^ 2) }
                            pcall(function() rec.name = tostring(ent:GetClassName()) end)
                            pcall(function() rec.open = ent:IsOpen() end)
                            pcall(function() rec.locked = ent:IsLocked() end)
                            pcall(function() rec.name = rec.name .. '/' .. GetLocalizedText(tostring(ent:GetDisplayName())) end)
                            out[#out + 1] = rec
                            pcall(function() lastDoors[#out] = ent:GetEntityID() end)
                        end
                    end
                end
            end
        end
        table.sort(out, function(a, b) return a.d < b.d end)
        resp.ok, resp.doors = true, out
        journal(string.format('OK   doors : %d dispositif(s)', #out))
        return resp
    elseif cmd.cmd == 'door_open' then
        -- OUVRIR une porte par script (plusieurs API essayees ; on journalise celles qui marchent)
        local eid = lastDoors[cmd.x or -1]
        if not eid then resp.reason = 'index inconnu (refaire doors)'; return resp end
        local ent = nil
        pcall(function() ent = Game.FindEntityByID(eid) end)
        if not ent then resp.reason = 'porte dechargee (refaire doors)'; return resp end
        journal(string.format('RUN  door_open idx=%d', cmd.x))
        local okList = {}
        local tries = {
            { 'OpenDoor', function() ent:OpenDoor() end },
            { 'ForceOpen', function() ent:ForceOpen() end },
            { 'PS.OpenDoor', function() ent:GetDevicePS():OpenDoor() end },
            { 'PS.ForceOpen', function() ent:GetDevicePS():ForceOpen() end },
            { 'PS.SetIsLocked(false)', function() ent:GetDevicePS():SetIsLocked(false) end },
            { 'PS.Unlock', function() ent:GetDevicePS():Unlock() end },
            { 'PS.ToggleOpenOnDoor', function() ent:GetDevicePS():ToggleOpenOnDoor() end },
            { 'PS.ForceOpenDoor', function() ent:GetDevicePS():ForceOpenDoor() end },
            { 'PS.SetDoorOpen', function() ent:GetDevicePS():SetDoorOpen(true) end },
        }
        for _, t in ipairs(tries) do
            local okX = pcall(t[2])
            if okX then okList[#okList + 1] = t[1] end
            local nowOpen = false
            pcall(function() nowOpen = ent:IsOpen() end)
            if nowOpen then break end
        end
        local isOpen = nil
        pcall(function() isOpen = ent:IsOpen() end)
        resp.ok, resp.methodes, resp.open = true, okList, isOpen
        journal(string.format('OK   door_open : [%s] open=%s', table.concat(okList, ','), tostring(isOpen)))
        return resp
    elseif cmd.cmd == 'list_vendors' then
        -- marchands connus de la minimap (variantes Vendor/Shop/Ripperdoc/Junk), avec distance
        journal('RUN  list_vendors')
        local pos = player:GetWorldPosition()
        local out = {}
        local mappins = Game.GetMappinSystem():GetMappins(gamemappinsMappinTargetType.Minimap)
        if type(mappins) == 'table' then
            for i = 1, #mappins do
                local m = mappins[i]
                local v = ''
                pcall(function() v = tostring(m:GetVariant()):gsub('gamedataMappinVariant : ', ''):gsub(' %(%d+%)', '') end)
                local lv = v:lower()
                if lv:find('vendor') or lv:find('shop') or lv:find('ripper') or lv:find('junk') or lv:find('market') or lv:find('apartment') or lv:find('wardrobe') then
                    local rec = { variant = v }
                    pcall(function()
                        local w = m:GetWorldPosition()
                        rec.x, rec.y, rec.z = w.x, w.y, w.z
                        rec.dist = math.sqrt((w.x - pos.x) ^ 2 + (w.y - pos.y) ^ 2)
                    end)
                    if rec.dist then out[#out + 1] = rec end
                end
            end
        end
        table.sort(out, function(a, b) return a.dist < b.dist end)
        while #out > 12 do table.remove(out) end
        resp.ok, resp.vendors = true, out
        journal('OK   list_vendors : ' .. #out)
        return resp
    elseif cmd.cmd == 'list_quests' then
        -- liste des quetes ACTIVES avec, pour chaque objectif, position du marqueur et distance
        -- CRASH NATIF le 2026-09-11 07:05 dans cette commande (derniere ligne : RUN list_quests).
        -- Chaque sous-etape est journalisee AVANT execution pour designer l appel coupable.
        journal('RUN  list_quests')
        local jm = Game.GetJournalManager()
        -- JournalManager.GetQuests PLANTE LE JEU depuis Lua (2 crashs, 07:05 et 07:09) : on
        -- n enumere PAS le journal. On passe par les marqueurs de quete de la minimap
        -- (GetMappins, prouve sur), qui donnent directement position + variante.
        local pos = player:GetWorldPosition()
        local out = {}
        journal('RUN  list_quests.GetMappins')
        local mappins = Game.GetMappinSystem():GetMappins(gamemappinsMappinTargetType.Minimap)
        if type(mappins) == 'table' then
            for i = 1, #mappins do
                local m = mappins[i]
                local isQ = false
                pcall(function() isQ = m:IsQuestMappin() end)
                if isQ then
                    local rec = { quest = 'marqueur', hasMappin = true }
                    pcall(function() rec.text = tostring(m:GetVariant()):gsub('gamedataMappinVariant : ', ''):gsub(' %(%d+%)', '') end)
                    pcall(function()
                        local w = m:GetWorldPosition()
                        rec.x, rec.y, rec.z = w.x, w.y, w.z
                        rec.dist = math.sqrt((w.x - pos.x) ^ 2 + (w.y - pos.y) ^ 2)
                    end)
                    -- pseudo-hash stable : position arrondie (sert de cle de blocage cote Python)
                    if rec.x then rec.hash = math.floor(rec.x) * 100000 + math.floor(rec.y) end
                    -- niveau recommande : via l entree de journal du marqueur si une methode l expose
                    if rec.hash and questLvlCache[rec.hash] ~= nil then
                        rec.lvl = questLvlCache[rec.hash] or nil
                    else
                        local entry = nil
                        for _, mn in ipairs({ 'GetJournalEntry', 'GetEntry', 'GetQuestEntry', 'GetObjective', 'GetJournalPathHash', 'GetJournalEntryHash', 'GetQuestMappinData', 'GetPhase', 'IsTracked', 'GetTrackedState', 'GetDataHash' }) do
                            local okH, has = pcall(function() return m[mn] ~= nil end)
                            if not mappinProbeDone then journal('OK   mappin.' .. mn .. ' existe=' .. tostring(okH and has) .. (okH and '' or (' err=' .. tostring(has)))) end
                            if okH and has then
                                journal('RUN  list_quests.mappin.' .. mn)
                                local okE, en = pcall(function() return m[mn](m) end)
                                journal('OK   list_quests.mappin.' .. mn .. ' -> ' .. tostring(okE and en ~= nil))
                                if okE and en then entry = en; break end
                            end
                        end
                        mappinProbeDone = true
                        if entry then
                            local okL, lv = pcall(function() return questLevelOf(jm, entry, rec.hash) end)
                            if okL then rec.lvl = lv end
                        else
                            questLvlCache[rec.hash] = false
                        end
                    end
                    if rec.dist then out[#out + 1] = rec end
                end
            end
        end
        journal('OK   list_quests : ' .. #out .. ' marqueurs de quete')
        local quests = {}
        for i = 1, 0 do
            local q = quests[i]
            journal('RUN  list_quests.quest ' .. i .. ' GetTitle')
            local okQ, titleQ = pcall(function() return GetLocalizedText(tostring(q:GetTitle(jm))) end)
            journal('RUN  list_quests.quest ' .. i .. ' GetQuestObjectives')
            local okO, objs = pcall(function() return jm:GetQuestObjectives(q, filter) end)
            if okO and type(objs) == 'table' then
                for j = 1, #objs do
                    local o = objs[j]
                    local rec = { quest = okQ and titleQ or '?', hasMappin = false }
                    journal('RUN  list_quests.quest ' .. i .. ' obj ' .. j .. ' hash')
                    pcall(function() rec.hash = jm:GetEntryHash(o) end)
                    journal('RUN  list_quests.quest ' .. i .. ' obj ' .. j .. ' description')
                    pcall(function() rec.text = GetLocalizedText(tostring(o:GetDescription())):sub(1, 80) end)
                    journal('RUN  list_quests.quest ' .. i .. ' obj ' .. j .. ' mappin')
                    pcall(function()
                        local parent = jm:GetParentEntry(o)
                        if parent then
                            local m = Game.GetMappinSystem():GetMappinFromObjective(parent, o)
                            if m then
                                local w = m:GetWorldPosition()
                                rec.hasMappin = true; rec.x, rec.y, rec.z = w.x, w.y, w.z
                                rec.dist = math.sqrt((w.x - pos.x) ^ 2 + (w.y - pos.y) ^ 2)
                            end
                        end
                    end)
                    rec.tracked = (trackedHash ~= nil and rec.hash == trackedHash)
                    if rec.hash then out[#out + 1] = rec end
                end
            end
        end
        resp.ok, resp.quests = true, out
        journal(string.format('OK   list_quests : %d quete(s), %d objectif(s)', #quests, #out))
        return resp
    elseif cmd.cmd == 'track' then
        journal('RUN  track ' .. tostring(cmd.hash))
        local jm = Game.GetJournalManager()
        local e = jm:GetEntry(cmd.hash)
        if not e then resp.reason = 'objectif introuvable pour ce hash'; return resp end
        jm:TrackEntry(e)
        resp.ok = true
        journal('OK   track')
        return resp
    elseif cmd.cmd == 'path_to_quest' then
        target, err = questTarget()
    elseif cmd.cmd == 'path_to' then
        target = Vector4.new(cmd.x, cmd.y, cmd.z or player:GetWorldPosition().z, 1.0)
    else
        err = 'commande inconnue: ' .. tostring(cmd.cmd)
    end
    if not target then resp.reason = err; journal('CMD ' .. tostring(cmd.seq) .. ' echec: ' .. tostring(err)); return resp end
    resp.target = { x = target.x, y = target.y, z = target.z }
    local avoid = nil
    if cmd.cmd == 'path_to_quest' and cmd.x and cmd.y then avoid = { x = cmd.x, y = cmd.y } end
    local path, perr, partial = computePath(player, target, avoid)
    if not path then resp.reason = perr; journal('CMD ' .. cmd.seq .. ' pas de chemin: ' .. tostring(perr)); return resp end
    local pts = {}
    for i = 1, #path.path do local w = path.path[i]; pts[i] = { w.x, w.y, w.z } end
    resp.ok, resp.points, resp.partial, resp.length = true, pts, partial, path:CalculateLength()
    journal(string.format('CMD %d ok: %d pts, %.1f m, partial=%s', cmd.seq, #pts, resp.length, tostring(partial)))
    return resp
end

-- Canal principal : SQLite (CET pre-ouvre `db` sur <mod>/db.sqlite3). Python INSERT,
-- Lua SELECT. Evite toute lecture de fichier cote Lua (f:read est nil dans le sandbox :
-- "attempt to call a nil value", journalise le 2026-09-10).
local function readCommandFromDb()
    if not dbReady then return nil end
    local found = nil
    for row in db:nrows('SELECT seq, cmd, x, y, z, hash FROM cmd ORDER BY seq DESC LIMIT 1') do
        found = { seq = row.seq, cmd = row.cmd, x = row.x, y = row.y, z = row.z, hash = row.hash }
    end
    return found
end

local fileDiagDone = false
local function readCommandFromFile()
    local f = io.open('cmd.json', 'r')
    if not f then return nil end
    if not fileDiagDone then
        fileDiagDone = true
        journal(string.format('FILEDIAG type(f)=%s read=%s lines=%s seek=%s',
            type(f), type(f.read), type(f.lines), type(f.seek)))
    end
    local body = nil
    if type(f.read) == 'function' then body = f:read('*a') end
    if body == nil and type(f.lines) == 'function' then
        local parts = {}
        for l in f:lines() do parts[#parts + 1] = l end
        body = table.concat(parts, '\n')
    end
    f:close()
    if not body or #body == 0 then return nil end
    return body
end

pollCommands = function(player, dt)
    cmdAcc = cmdAcc + dt
    if cmdAcc < CMD_PERIOD then return end
    cmdAcc = 0.0
    local fromDb = readCommandFromDb()
    if fromDb then
        if fromDb.seq ~= lastCmdSeq then
            lastCmdSeq = fromDb.seq
            local okH, resp = pcall(handleCommand, player, fromDb)
            if not okH then
                journal('FAIL cmd ' .. tostring(fromDb.cmd) .. ' seq=' .. tostring(fromDb.seq) .. ' : ' .. tostring(resp))
                resp = { seq = fromDb.seq, ok = false, reason = 'erreur: ' .. tostring(resp), seqEnd = fromDb.seq }
            end
            writePath(resp)
        end
        return
    end
    local body = readCommandFromFile()
    if not body then return end
    -- json.decode n est pas garanti dans le sandbox : parseur de secours par motifs
    local cmd = nil
    if type(json) == 'table' and type(json.decode) == 'function' then
        local ok, d = pcall(json.decode, body); if ok and type(d) == 'table' then cmd = d end
    end
    if not cmd then
        cmd = {
            seq = tonumber(body:match('"seq"%s*:%s*(%-?%d+)')),
            cmd = body:match('"cmd"%s*:%s*"([^"]+)"'),
            x = tonumber(body:match('"x"%s*:%s*(%-?[%d%.]+)')),
            y = tonumber(body:match('"y"%s*:%s*(%-?[%d%.]+)')),
            z = tonumber(body:match('"z"%s*:%s*(%-?[%d%.]+)')),
        }
    end
    if not cmd.seq or not cmd.cmd or cmd.seq == lastCmdSeq then return end
    lastCmdSeq = cmd.seq
    local okH, resp = pcall(handleCommand, player, cmd)
    if not okH then resp = { seq = cmd.seq, ok = false, reason = 'erreur: ' .. tostring(resp), seqEnd = cmd.seq } end
    writePath(resp)
end


-- ============================ FENETRE IN-GAME (overlay CET) ============================
-- Reglages de l agent saisis dans le jeu : fournisseur du modele, cle API, modeles, comportements.
-- Ecrits dans agent_config.json (dossier du mod) ; l agent Python les lit en priorite.
local ui = { open = false, provider = 1, key = '', model = 'llama3.2:latest', openai_model = 'gpt-4o-mini',
             anthropic_model = 'claude-haiku-4-5-20251001', minutes = 20, saved = '',
             radio = true, driving = true, rescue = true, sell = true, ripperdoc = true, buffs = true,
             stealth = true, fasttravel = true, phone = true, sms = true, appearance = true, recipes = true, courage = 3, style = 1, aggro = 2 }
local uiCourage = { 'prudent', 'equilibre', 'temeraire' }
local uiStyle = { 'melee', 'mixte', 'distance' }
local uiAggro = { 'defensif', 'normal', 'chasseur' }
local uiProviders = { 'ollama', 'openai', 'anthropic' }
local function uiLoad()
    local f = io.open('agent_config.json', 'r')
    if not f then return end
    local txt = f:read('*a'); f:close()
    local ok, d = pcall(json.decode, txt)
    if not ok or type(d) ~= 'table' then return end
    for i, p in ipairs(uiProviders) do if d.provider == p then ui.provider = i end end
    ui.key = d.api_key or ui.key
    ui.model = d.model or ui.model
    ui.openai_model = d.openai_model or ui.openai_model
    ui.anthropic_model = d.anthropic_model or ui.anthropic_model
    ui.minutes = d.minutes or ui.minutes
    if type(d.features) == 'table' then
        for _, k in ipairs({ 'radio', 'driving', 'rescue', 'sell', 'ripperdoc', 'buffs', 'stealth', 'fasttravel', 'phone', 'sms', 'appearance', 'recipes' }) do
            if d.features[k] ~= nil then ui[k] = d.features[k] end
        end
    end
    for i, v in ipairs(uiCourage) do if d.courage == v then ui.courage = i end end
    for i, v in ipairs(uiStyle) do if d.style == v then ui.style = i end end
    for i, v in ipairs(uiAggro) do if d.aggro == v then ui.aggro = i end end
end
local function uiSave()
    local d = { provider = uiProviders[ui.provider], api_key = ui.key, model = ui.model, openai_model = ui.openai_model,
                anthropic_model = ui.anthropic_model, minutes = ui.minutes,
                courage = uiCourage[ui.courage], style = uiStyle[ui.style], aggro = uiAggro[ui.aggro],
                features = { radio = ui.radio, driving = ui.driving, rescue = ui.rescue, sell = ui.sell, ripperdoc = ui.ripperdoc, buffs = ui.buffs,
                             stealth = ui.stealth, fasttravel = ui.fasttravel, phone = ui.phone, sms = ui.sms, appearance = ui.appearance, recipes = ui.recipes } }
    local f = io.open('agent_config.json', 'w')
    if f then f:write(json.encode(d)); f:close(); ui.saved = 'enregistre ' .. os.date('%H:%M:%S') else ui.saved = 'echec d ecriture' end
end
pcall(uiLoad)
registerForEvent('onOverlayOpen', function() ui.open = true end)
registerForEvent('onOverlayClose', function() ui.open = false end)
registerForEvent('onDraw', function()
    -- RELAIS PENDANT LE BREACH PROTOCOL : le jeu est en pause (onUpdate ne tourne plus) mais onDraw continue.
    -- On sert les commandes Python et on exporte un etat (avec breach) pour que l agent puisse resoudre la grille.
    pcall(function()
        local now = os.clock()
        local dtd = (lastDrawClock > 0) and (now - lastDrawClock) or 0.016
        lastDrawClock = now
        if now - lastUpdateClock < 0.3 then return end          -- onUpdate tourne : rien a faire ici
        local stt, tm = breachState()
        if not (breachCtrl ~= nil or stt == 1) then return end   -- pas de mini-jeu ouvert : les autres menus restent "figes" (garde Python)
        local player = Game.GetPlayer()
        if not player or not dbReady then return end
        local okP, errP = pcall(pollCommands, player, dtd)
        if not okP and tostring(errP) ~= lastPollErr then lastPollErr = tostring(errP); journal('POLL(draw) erreur: ' .. lastPollErr) end
        if fh and lastExport then
            seq = seq + 1
            lastExport.seq, lastExport.seqEnd = seq, seq
            lastExport.breach = { state = stt, timer = tm, last = breachLastPos(), ctrl = (breachCtrl ~= nil) }
            lastExport.paused = true
            lastExport.combat, lastExport.enemies, lastExport.dialog, lastExport.interact = false, nil, nil, nil
            local okE, s = pcall(json.encode, lastExport)
            if okE and #s < STATE_WIDTH then fh:seek('set', 0); fh:write(pad(s, STATE_WIDTH) .. '\n'); fh:flush() end
        end
    end)
    if not ui.open then return end
    if ImGui.Begin('CyberpunkAgent') then
        ImGui.Text('Modele de decision')
        for i, p in ipairs(uiProviders) do
            if ImGui.RadioButton(p, ui.provider == i) then ui.provider = i end
            if i < #uiProviders then ImGui.SameLine() end
        end
        if ui.provider > 1 then
            ui.key = ImGui.InputText('Cle API', ui.key, 256, ImGuiInputTextFlags.Password)
        end
        ui.model = ImGui.InputText('Modele Ollama', ui.model, 128)
        ui.openai_model = ImGui.InputText('Modele OpenAI', ui.openai_model, 128)
        ui.anthropic_model = ImGui.InputText('Modele Anthropic', ui.anthropic_model, 128)
        ImGui.Separator()
        ImGui.Text('Comportements de V')
        ui.radio = ImGui.Checkbox('Radio de temps en temps', ui.radio)
        ui.driving = ImGui.Checkbox('Conduire quand l objectif est loin', ui.driving)
        ui.rescue = ImGui.Checkbox('Intervenir dans les agressions', ui.rescue)
        ui.sell = ImGui.Checkbox('Vendre la camelote / acheter des soins', ui.sell)
        ui.ripperdoc = ImGui.Checkbox('Charcudoc (cyberware)', ui.ripperdoc)
        ui.buffs = ImGui.Checkbox('Buffs avant le combat', ui.buffs)
        ui.stealth = ImGui.Checkbox('Discretion (approche accroupie, elimination furtive)', ui.stealth)
        ui.fasttravel = ImGui.Checkbox('Voyage rapide (bornes)', ui.fasttravel)
        ui.phone = ImGui.Checkbox('Repondre aux appels', ui.phone)
        ui.sms = ImGui.Checkbox('Lire et repondre aux SMS', ui.sms)
        ui.appearance = ImGui.Checkbox('Changer d apparence au miroir de temps en temps', ui.appearance)
        ui.recipes = ImGui.Checkbox('Acheter et apprendre des plans de craft', ui.recipes)
        ImGui.Separator()
        ImGui.Text('Temperament')
        ImGui.Text('Courage :'); ImGui.SameLine()
        for i, v in ipairs(uiCourage) do if ImGui.RadioButton(v, ui.courage == i) then ui.courage = i end; if i < #uiCourage then ImGui.SameLine() end end
        ImGui.Text('Style :'); ImGui.SameLine()
        for i, v in ipairs(uiStyle) do if ImGui.RadioButton(v .. '##s', ui.style == i) then ui.style = i end; if i < #uiStyle then ImGui.SameLine() end end
        ImGui.Text('Agressivite :'); ImGui.SameLine()
        for i, v in ipairs(uiAggro) do if ImGui.RadioButton(v .. '##a', ui.aggro == i) then ui.aggro = i end; if i < #uiAggro then ImGui.SameLine() end end
        ui.minutes = ImGui.InputInt('Duree de session (min)', ui.minutes)
        ImGui.Separator()
        if ImGui.Button('Enregistrer') then pcall(uiSave) end
        ImGui.SameLine(); ImGui.Text(ui.saved)
        ImGui.Text('Lance ensuite CyberpunkAgent.exe (F11 pause, F12 arret).')
    end
    ImGui.End()
end)

registerForEvent('onShutdown', function()
    if fh then fh:close(); fh = nil end
end)
