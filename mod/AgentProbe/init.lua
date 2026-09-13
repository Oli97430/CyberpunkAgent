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

registerForEvent('onInit', function()
    os.remove('probe_progress.txt')
    journal('onInit ' .. os.date('%H:%M:%S'))
    fh = io.open('state.bin', 'w+b')
    journal('state.bin ouvert : ' .. tostring(fh ~= nil))
    -- table de commandes SQLite (canal Python -> Lua). NB: db:exec = SQLite, pas un shell.
    local okDb, errDb = pcall(function()
        db:exec('CREATE TABLE IF NOT EXISTS cmd (seq INTEGER PRIMARY KEY, cmd TEXT, x REAL, y REAL, z REAL, hash INTEGER)')
        pcall(function() db:exec('ALTER TABLE cmd ADD COLUMN hash INTEGER') end)
        -- ignorer une commande restee dans la table depuis une session precedente
        local n = 0
        for row in db:nrows('SELECT MAX(seq) AS m FROM cmd') do if row.m then lastCmdSeq = row.m; n = 1 end end
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
                            local rec = { x = ep.x, y = ep.y, z = ep.z, d = math.sqrt(dxE * dxE + dyE * dyE), near = true }
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
        if not inCombat then
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
        -- TELEPHONE : appel entrant / en cours (UI_ComDevice.callInformation : callPhase, contactName)
        local phone = nil
        pcall(function()
            local def = GetAllBlackboardDefs().UI_ComDevice
            if not def then return end
            local bb = Game.GetBlackboardSystem():Get(def)
            if not bb then return end
            local p = {}
            pcall(function()
                local ci = bb:GetVariant(def.callInformation)
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
            pcall(function() p.activeCall = bb:GetBool(def.PhoneCallActive) end)
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
        if not inCombat then
            pcall(function()
                local qv = Game['TSQ_ALL;']()
                qv.maxDistance = 25.0
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
                                    list[#list + 1] = rec
                                end
                            end
                        end
                    end
                    table.sort(list, function(a, b) return a.d < b.d end)
                    while #list > 4 do table.remove(list) end
                    if #list > 0 then vehicles = list end
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
        if not inCombat then
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
                end
            end)
        end
        -- CORPS : les requetes de ciblage excluent souvent les morts. On memorise la derniere
        -- position de chaque ennemi vu (cle = position arrondie) ; quand il n est plus
        -- renvoye vivant, il devient un "corps" exporte pendant 90 s (pour le loot).
        local nowT = os.clock()
        local seen = {}
        if enemies then
            for i = 1, #enemies do
                local e = enemies[i]
                local key = string.format('%d:%d', math.floor(e.x / 2), math.floor(e.y / 2))
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
                 hp = hp, level = playerLevel, combat = inCombat, vehicle = inVehicle, carrying = carrying, locomotion = locomotion, upperBody = upperBody,
                 lootPanel = lootPanel, lootCount = lootCount, loot = loot, lookat = lookat, crimes = lastCrimes, vehicles = vehicles, buffs = buffs, phone = phone,
                 enemies = enemies, bodies = bodies, npcs = npcs, qh = qh, dialog = dlg, interact = inter, quest = quest, seqEnd = seq }
    end)
    -- journal une fois par changement de dialogue : structure reelle des hubs (pour la competence)
    if ok and data then
        local sig = data.dialog and (data.dialog.title .. '|' .. #data.dialog.choices .. '|' .. tostring(data.dialog.sel)) or ''
        if sig ~= lastDialogSig then
            lastDialogSig = sig
            if data.dialog then journal('DIALOG ' .. json.encode(data.dialog)) else journal('DIALOG ferme') end
        end
    end
    if ok and data then
        fh:seek('set', 0)
        fh:write(pad(json.encode(data), STATE_WIDTH) .. '\n')
        fh:flush()
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
local CMD_PERIOD = 0.25
local cmdAcc, lastCmdSeq = 0.0, -1

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
local lastVendorStock = {}                   -- ItemID par index de la derniere liste de stock marchand
-- marchand PRESENT (< 6 m) : une entree par entite, priorite a IsVendor()
local function findNearbyVendor(player)
    local q = Game['TSQ_NPC;']()
    q.maxDistance = 6.0
    q.filterObjectByDistance = true
    pcall(function() q.testedSet = TargetingSet.Complete end)
    local okT, parts = Game.GetTargetingSystem():GetTargetParts(player, q)
    local vendor = nil
    local selfKey = nil
    pcall(function() selfKey = tostring(player:GetEntityID().hash) end)
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
                local isV = false
                pcall(function() isV = ent:IsVendor() end)
                if isV then return ent end
                if not vendor then vendor = ent end
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
        journal(string.format('RUN  sell idx=%d qty=%d', cmd.x, cmd.y or 1))
        local q = Game['TSQ_NPC;']()
        q.maxDistance = 6.0
        q.filterObjectByDistance = true
        pcall(function() q.testedSet = TargetingSet.Complete end)
        local okT, parts = Game.GetTargetingSystem():GetTargetParts(player, q)
        local vendor = nil
        local selfKey = nil
        pcall(function() selfKey = tostring(player:GetEntityID().hash) end)
        if okT and parts then
            local seenEnt = {}   -- une entree par ENTITE (GetTargetParts renvoie une partie par zone du corps)
            for i = 1, #parts do
                local comp = TS_TargetPartInfo.GetComponent(parts[i])
                local ent = comp and comp:GetEntity() or nil
                if ent then
                    local okH, h = pcall(function() return ent:GetEntityID().hash end)
                    local key = okH and tostring(h) or tostring(ent)
                    if seenEnt[key] or key == selfKey then ent = nil else seenEnt[key] = true end
                end
                if ent then
                    local isV = false
                    pcall(function() isV = ent:IsVendor() end)
                    if isV then vendor = ent; break end
                    if not vendor then vendor = ent end
                end
            end
        end
        if not vendor then resp.reason = 'aucun marchand a portee'; journal('FAIL sell : aucun marchand'); return resp end
        local price = 0
        local okP, pr = pcall(function() return RPGManager.CalculateSellPrice(vendor, id) end)
        if okP and pr then price = pr else
            pcall(function() price = math.floor(Game.GetTransactionSystem():GetItemData(player, id):GetStatValueByType(gamedataStatType.Price) * 0.15) end)
        end
        local qty = cmd.y or 1
        local ts = Game.GetTransactionSystem()
        local okX = pcall(function() ts:TransferItem(player, vendor, id, qty) end)
        if not okX then pcall(function() ts:RemoveItem(player, id, qty) end) end
        local total = math.floor(price * qty)
        pcall(function() ts:GiveItem(player, MarketSystem.Money(), total) end)
        resp.ok, resp.price, resp.total = true, price, total
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
        lastVendorStock = {}
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
        local qty = cmd.y or 1
        journal(string.format('RUN  buy idx=%d qty=%d', cmd.x, qty))
        local ts = Game.GetTransactionSystem()
        local price = buyPrice(vendor, player, id)
        local total = math.floor(price * qty)
        local money = 0
        pcall(function() money = ts:GetItemQuantity(player, MarketSystem.Money()) end)
        if money < total then resp.reason = string.format('pas assez d eddies (%d < %d)', money, total); journal('FAIL buy : ' .. resp.reason); return resp end
        local okX = pcall(function() ts:TransferItem(vendor, player, id, qty) end)
        if not okX then
            local okG = pcall(function() ts:GiveItem(player, id, qty) end)
            if not okG then resp.reason = 'transfert impossible'; journal('FAIL buy : transfert'); return resp end
        end
        pcall(function() ts:RemoveItem(player, MarketSystem.Money(), total) end)
        resp.ok, resp.price, resp.total = true, price, total
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
                local okX = pcall(function() ts:TransferItem(best, player, id, 1) end)
                if okX then moved = 1; methods[#methods + 1] = 'TransferItem(drop)' else
                    local okG = pcall(function() ts:GiveItem(player, id, 1) end)
                    if okG then
                        moved = 1; methods[#methods + 1] = 'GiveItem+Dispose'
                        pcall(function() best:Dispose() end)
                    end
                end
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
        for i = 1, #pts do
            local p = pts[i]
            local rec = { i = i }
            pcall(function() rec.name = GetLocalizedText(tostring(p:GetPointDisplayName())) end)
            pcall(function() rec.district = tostring(p:GetDistrictDisplayName()) end)
            pcall(function()
                local w = p:GetMarkerPosition()
                if w then rec.x, rec.y, rec.z = w.x, w.y, w.z; rec.d = math.sqrt((w.x - pos.x) ^ 2 + (w.y - pos.y) ^ 2) end
            end)
            lastFastTravel[i] = p
            out[#out + 1] = rec
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
        local did = {}
        if pcall(function() fts:PerformFastTravel(p, player) end) then did[#did + 1] = 'PerformFastTravel(p, player)' else
            if pcall(function() fts:PerformFastTravel(p) end) then did[#did + 1] = 'PerformFastTravel(p)' end
        end
        resp.ok, resp.methodes = (#did > 0), did
        if #did == 0 then resp.reason = 'aucune methode de voyage acceptee' end
        journal('OK   fast_travel : [' .. table.concat(did, ',') .. ']')
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
                            lastDoors[#out] = ent
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
        local ent = lastDoors[cmd.x or -1]
        if not ent then resp.reason = 'index inconnu (refaire doors)'; return resp end
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
                if lv:find('vendor') or lv:find('shop') or lv:find('ripper') or lv:find('junk') or lv:find('market') then
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
                        for _, mn in ipairs({ 'GetJournalEntry', 'GetEntry', 'GetQuestEntry', 'GetObjective' }) do
                            local okH, has = pcall(function() return m[mn] ~= nil end)
                            if okH and has then
                                journal('RUN  list_quests.mappin.' .. mn)
                                local okE, en = pcall(function() return m[mn](m) end)
                                journal('OK   list_quests.mappin.' .. mn .. ' -> ' .. tostring(okE and en ~= nil))
                                if okE and en then entry = en; break end
                            end
                        end
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
            if not okH then resp = { seq = fromDb.seq, ok = false, reason = 'erreur: ' .. tostring(resp), seqEnd = fromDb.seq } end
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
             radio = true, driving = true, rescue = true, sell = true, ripperdoc = true, buffs = true }
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
        for _, k in ipairs({ 'radio', 'driving', 'rescue', 'sell', 'ripperdoc', 'buffs' }) do
            if d.features[k] ~= nil then ui[k] = d.features[k] end
        end
    end
end
local function uiSave()
    local d = { provider = uiProviders[ui.provider], api_key = ui.key, model = ui.model, openai_model = ui.openai_model,
                anthropic_model = ui.anthropic_model, minutes = ui.minutes,
                features = { radio = ui.radio, driving = ui.driving, rescue = ui.rescue, sell = ui.sell, ripperdoc = ui.ripperdoc, buffs = ui.buffs } }
    local f = io.open('agent_config.json', 'w')
    if f then f:write(json.encode(d)); f:close(); ui.saved = 'enregistre ' .. os.date('%H:%M:%S') else ui.saved = 'echec d ecriture' end
end
pcall(uiLoad)
registerForEvent('onOverlayOpen', function() ui.open = true end)
registerForEvent('onOverlayClose', function() ui.open = false end)
registerForEvent('onDraw', function()
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
