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
local slowT, slowLoot, slowVehicles, slowNpcs, slowTraffic = -99.0, nil, nil, nil, 0
local playerVehRecs, playerVehRecsT = {}, -999.0   -- records (TweakDB) des vehicules de V, rafraichis toutes les 15 s
local deadSince, deadReloaded, lastCmdClock = nil, false, -1e9   -- mort de V : rechargement automatique si l agent est actif
local vehCallT = -999.0                       -- os.clock() du dernier vehicle_call : export des vehicules de V a 400 m pendant 45 s   -- scans larges (TSQ_ALL) a 4 Hz, pas 20
local pendingDirective = nil                  -- directive tapee dans le panneau (ou reçue par Telegram cote Python) : lue une fois par get_directive
local hudShow = true                          -- HUD « Terminator » (etat de V) par-dessus le jeu
local hudData = nil                           -- etat de l IA publie par Python (table SQLite hud), relu 2x/s
local hudReadT, hudHexT, hudLastErr = -99.0, -99.0, nil
local hudHex = {}
local hudTrack, hudKilled = {}, {}            -- hostiles vus debout (cle -> EntityID, instant) / deja comptes
local hudKills, hudKillsTotal, hudSessionT0, hudCheckT = 0, 0, nil, -99.0
local hudLocks = false                        -- cadres de verrouillage sur les hostiles (12/10 : retires par defaut, demande Olivier ; case du panneau CET)
local hudLockTargets = {}                     -- { sx, sy, d, police } calcules dans onUpdate, dessines dans onDraw
local hudEvents = {}                          -- evenements cote mod (pertes) : { instant, texte }
local hudPos = 'milieu_droite'                -- position du HUD (panneau CET) ; « libre » : glisser a la souris
local hudPosList = { 'milieu_droite', 'milieu_gauche', 'bas_gauche', 'bas_droite', 'libre' }
local hudPosLabels = { 'milieu droite', 'milieu gauche', 'bas gauche', 'bas droite', 'libre (glisser)' }
local hudW, hudH = 480, 420                   -- taille mesuree du HUD (auto) : ancrage par le bord droit
local hudScan, hudScanCache, hudScanN = nil, {}, 0   -- analyse de l objet sous le reticule (hors export Python)
local lastFtPoints = {}                  -- positions des bornes de voyage rapide (garde du teleport)
local lastVendorKey, lastVendorQty = nil, {}   -- marchand de vendor_stock (hash) et quantites en stock
local breachCtrl = nil
local breachSel = nil                        -- { x, y, n, t } : derniere selection signalee par le jeu                   -- HackingMinigameGameController capture a l ouverture du Breach Protocol
local bdClues, bdLastClueSig, bdFocusCache = {}, '', nil   -- danse sensorielle : indices de la timeline vus, entites-indices
local lastUpdateClock, lastDrawClock = 0.0, 0.0   -- onUpdate s arrete dans les menus (dont le mini-jeu) : onDraw prend le relais
local lastExport = nil                   -- derniere table d etat ecrite (reutilisee par onDraw pendant le mini-jeu)
local enemyDiag, lastEnemyDiag = '', ''
local aliveMemory, bodyMemory = {}, {}      -- derniere position des ennemis vus / corps (loot)
local lastInventory = {}                    -- ItemID par index de la derniere liste d inventaire
local qhListLogged = false                  -- structure de la liste des hacks journalisee une fois
local qhPopList, qhPopT, qhPopLogged = nil, -99.0, false   -- liste REELLEMENT affichee par le panneau (hook PopulateData)
local qhPopOk = false                      -- au moins une entree lisible (sinon : liste en cache)
local qhOpenT, qhWasOpen = -99.0, false    -- ouverture du panneau en cours (la liste du hook doit etre de ce panneau)
local resCache, resT, resLogged = nil, -99.0, ''     -- charges de V (soins, grenades, lance-projectiles), 4 fois/s ; dernier journalise
local function enemyOrder(a, b)
    -- 24/09 : les VIVANTS d abord, puis les plus proches (les morts proches evincaient les vivants plus loin) ;
    -- un ennemi assomme (down) est range avec les morts
    local da, db = (a.dead or a.down) and true or false, (b.dead or b.down) and true or false
    if da ~= db then return not da end
    return a.d < b.d
end
local lastRecipes = {}                       -- TweakDBID par index de la derniere liste de recettes
local craftDiagDone = false


local lootClassesLogged = false             -- classes d objets lootables vues, journalisees une fois
local lookatLogged = false
AgentLosLogged = false                        -- 12/10 : 1re mesure de ligne de vue journalisee (GLOBAL : limite LuaJIT de 60 upvalues)
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

-- 12/10 GRENADE ADAPTEE (Olivier : « V doit adapter les projectiles qu il lance a l ennemi ») -- fonctions et tables
-- GLOBALES : aucune upvalue de plus dans onUpdate / handleCommand (limite LuaJIT de 60).
-- Famille d un objet par les TAGS de son record (methode du jeu : fragGrenade.script GetGrenadeType)
AGENT_GREN_TAGS = { {'EMPGrenade','emp'}, {'IncendiaryGrenade','fire'}, {'BiohazardGrenade','bio'}, {'FlashGrenade','flash'},
                    {'SmokeGrenade','smoke'}, {'CuttingGrenade','cut'}, {'ReconGrenade','recon'}, {'PiercingGrenade','pierce'},
                    {'SonicGrenade','sonic'}, {'FragGrenade','frag'} }
AgentGrenKindCache = AgentGrenKindCache or {}
function AgentGrenKind(tdbid)
    local ck = tostring(tdbid)
    local k = AgentGrenKindCache[ck]
    if k ~= nil then return k or nil end
    k = false
    pcall(function()
        local r = TweakDBInterface.GetItemRecord(tdbid)
        local t = tostring(r:ItemType():Type())
        if t:find('Cyb_Launcher') then k = 'launcher'
        elseif t:find('Cyb_Ability') then k = 'ability'
        elseif t:find('Gad_Grenade') then
            k = 'frag'                                   -- defaut du jeu ; Ozob's Nose n a pas le tag FragGrenade
            for _, p in ipairs(AGENT_GREN_TAGS) do
                local ok, v = pcall(function() return r:TagsContains(p[1]) end)               -- tweakDBRecords.script:5155
                if not ok then ok, v = pcall(function() return r:TagsContains(CName.new(p[1])) end) end
                if ok and v == true then k = p[2]; break end
            end
        end
    end)
    AgentGrenKindCache[ck] = k
    local sid = ck; pcall(function() sid = TDBID.ToStringDEBUG(tdbid) end)
    journal('GKIND ' .. sid .. ' -> ' .. tostring(k))                                       -- une fois par record : validation
    return k or nil
end

-- Type et faiblesses d un ennemi (cache 10 s par entite) : kind human/android/drone/mech/robot/turret, emp (bonus IEM du
-- jeu : emp.tweak:373-405 = NPCIsMechanical OU netrunner OU exo), imm (immunites E/B/P/F), boss, net
AgentFoeCache, AgentFoeLogged, AgentFoePurgeT = AgentFoeCache or {}, AgentFoeLogged or 0, 0
function AgentFoeInfo(ent, rec)
    local key = tostring(ent:GetEntityID().hash)
    local now = os.clock()
    local fi = AgentFoeCache[key]
    if not fi or now - fi.t > 10.0 then
        local first = (fi == nil)
        fi = { t = now }
        local ss, eid = Game.GetStatsSystem(), ent:GetEntityID()
        local function st(n)
            local ok, v = pcall(function() return ss:GetStatValue(eid, gamedataStatType[n]) end)   -- statsSystem.script:34
            return (ok and type(v) == 'number') and v or 0
        end
        local npct, rar = '?', '?'
        pcall(function() if ent:IsTurret() then fi.kind = 'turret' end end)                       -- gameObject.script:1776
        if not fi.kind then
            pcall(function() npct = tostring(ent:GetNPCType()) end)                                 -- scriptedPuppet.script:1419
            if npct:find('Android') then fi.kind = 'android'
            elseif npct:find('Drone') then fi.kind = 'drone'
            elseif npct:find('Mech') then fi.kind = 'mech'
            elseif npct:find('Cerberus') or npct:find('Chimera') or npct:find('Spiderbot') then fi.kind = 'robot'
            elseif npct:find('Human') then fi.kind = 'human' end
            pcall(function() if fi.kind == 'human' and ent:IsMechanical() then fi.kind = 'robot' end end)   -- :1456
        end
        pcall(function() rar = tostring(ent:GetNPCRarity()) end)                                    -- puppet.script:95
        if rar:find('Boss') or rar:find('MaxTac') then fi.boss = true end
        local imm = ''
        if st('EMPImmunity') > 0 then imm = imm .. 'E' end
        if st('BurningImmunity') > 0 then imm = imm .. 'B' end
        if st('PoisonImmunity') > 0 then imm = imm .. 'P' end
        if st('BlindImmunity') > 0 then imm = imm .. 'F' end
        if imm ~= '' then fi.imm = imm end
        local net, exo = st('IsNetrunnerArchetype') > 0, st('IsExo') > 0
        if net then fi.net = true end
        if fi.kind == 'android' or fi.kind == 'drone' or fi.kind == 'mech' or net or exo then fi.emp = true end
        AgentFoeCache[key] = fi
        if first and AgentFoeLogged < 300 then
            AgentFoeLogged = AgentFoeLogged + 1
            journal(string.format('FOE %s d=%.0f %s kind=%s rar=%s imm=%s emp=%s net=%s exo=%s res(th/el/ch)=%.0f/%.0f/%.0f',
                tostring(rec.name), rec.d or 0, npct, tostring(fi.kind), rar, imm, tostring(fi.emp), tostring(net), tostring(exo),
                st('ThermalResistance'), st('ElectricResistance'), st('ChemicalResistance')))
        end
    end
    if now - AgentFoePurgeT > 60 then
        AgentFoePurgeT = now
        for k, v in pairs(AgentFoeCache) do if now - v.t > 120 then AgentFoeCache[k] = nil end end
    end
    rec.kind, rec.emp, rec.imm, rec.boss, rec.net = fi.kind, fi.emp, fi.imm, fi.boss, fi.net
end

-- 12/10 ARMES A DISTANCE ADAPTEES (globales : pas d upvalue de plus)
function AgentEnumName(v) return (tostring(v):gsub('^[%w_]+ : ', ''):gsub(' %(%-?%d+%)', '')) end
-- cle d un EXEMPLAIRE : hash TweakDB + rng_seed (champ CET non verifie : 0 si absent)
function AgentItemKey(id)
    local h, sd = nil, 0
    pcall(function() if ItemID.IsValid(id) then h = tonumber(id.id.hash) end end)
    if not h then return nil end
    pcall(function() sd = tonumber(id.rng_seed) or 0 end)
    return string.format('%.0f:%.0f', h, sd), h, sd
end
-- fiche d une arme a distance (tag RangedWeapon, base_weapon.tweak:15) ; l element d une arme a feu n agit que par ses
-- effets d etat (damageSystem.script:546-548) : on exporte les taux d application
function AgentRangedInfo(player, ts, edata, data, id, rec)
    local isR = false
    pcall(function() isR = data:HasTag('RangedWeapon') end)
    if not isR then return false end
    local S = gamedataStatType
    local function st(x) local ok, v = pcall(function() return data:GetStatValueByType(x) end); return (ok and type(v) == 'number') and v or 0 end
    local k, h, sd = AgentItemKey(id)
    rec.r, rec.key, rec.h, rec.seed = true, k, h, sd
    pcall(function() rec.evo = AgentEnumName(TweakDBInterface.GetWeaponItemRecord(ItemID.GetTDBID(id)):Evolution():Type()) end)
    local dm = { Physical = st(S.PhysicalDamage), Electric = st(S.ElectricDamage), Thermal = st(S.ThermalDamage), Chemical = st(S.ChemicalDamage) }
    local best, bv = 'Physical', dm.Physical
    for _, e in ipairs({ 'Electric', 'Thermal', 'Chemical' }) do if dm[e] > bv then best, bv = e, dm[e] end end
    rec.elem = best
    rec.rng, rec.maxr = st(S.EffectiveRange), st(S.MaximumRange)
    rec.mag, rec.cyc, rec.chg = st(S.MagazineCapacity), st(S.CycleTime), st(S.ChargeTime)
    rec.burn, rec.shock, rec.poison = st(S.BurningApplicationRate), st(S.ElectrocutedApplicationRate), st(S.PoisonedApplicationRate)
    rec.smart, rec.pierce = st(S.ItemRequiresSmartLink) > 0, st(S.TechPierceEnabled) > 0
    pcall(function() rec.ammo = ts:GetItemQuantity(player, ItemID.CreateQuery(TweakDBInterface.GetWeaponItemRecord(ItemID.GetTDBID(id)):Ammo():GetID())) end)
    pcall(function() rec.eq_ok = edata:IsEquippable(data) end)
    pcall(function()
        for _, p in ipairs(data:GetItemParts() or {}) do
            if TDBID.ToStringDEBUG(InnerItemData.GetStaticData(p):GetID()):find('TechMod1') then rec.emp = true end
        end
    end)
    return true
end

-- 12/10 GOUTS DE V EN VEHICULES (globales : pas d upvalue de plus). Identite = record TweakDB (cache par record).
AGENT_VEH_TIER = { Rayfield = 40, Herrera = 38, Porsche = 36, Yaiba = 34, Arch = 34, Quadra = 28, Mizutani = 22,
                   Villefort = 15, Chevillon = 14, Brennan = 12, Archer = 10, Militech = 8, Thorton = 6, Mahir = 4,
                   Makigai = 2, Kaukaz = 0, Delamain = 0, Zetatech = 0 }
AGENT_VEH_CLS = { Premium = 10, Sport = 8, Standard = 0, Poor = -15 }
AgentVehInfoCache = AgentVehInfoCache or {}
AgentVehInfoN = AgentVehInfoN or 0
AgentVehOccCache = AgentVehOccCache or {}
function AgentVehInfo(ent)
    local okI, recID = pcall(function() return ent:GetRecordID() end)                -- vehicles.script:66
    if not okI or recID == nil then return nil end
    local sid = tostring(recID); pcall(function() sid = TDBID.ToStringDEBUG(recID) end)
    local vi = AgentVehInfoCache[sid]
    if vi ~= nil then return vi end
    vi = { rid = (sid:gsub('^Vehicle%.', '')) }
    local r = nil
    pcall(function() r = TweakDBInterface.GetVehicleRecord(recID) end)                -- tweakDB.script:902
    if r then
        pcall(function() vi.mk = r:Manufacturer():EnumName() end)
        pcall(function() vi.vt = r:Type():EnumName() end)                           -- 'Car' / 'Bike'
        pcall(function() local u = r:VehicleUIData()
            vi.hp = math.floor(u:Horsepower() + 0.5); vi.kg = math.floor(u:Mass() + 0.5) end)
        local function tag(t)
            local ok, v = pcall(function() return r:TagsContains(t) end)
            if not ok then ok, v = pcall(function() return r:TagsContains(CName.new(t)) end) end
            return ok and v == true
        end
        if tag('Sport') then vi.sp = true end
        if tag('TriggerPrevention') then vi.cop = true end                           -- vehicles.script:398 : vol = crime
        pcall(function() for _, t in ipairs(r:VisualTags()) do
            local s = Game.NameToString(t)
            if s == 'NCPD' then vi.cop = true end
            if vi.cls == nil and AGENT_VEH_CLS[s] ~= nil then vi.cls = s end
        end end)
        pcall(function() if tostring(r:Affiliation():Type()):find('NCPD') then vi.cop = true end end)
    end
    pcall(function() local h = TweakDB:GetFlat(sid .. '.hijackDifficulty'); if h ~= nil then vi.hj = tostring(h) end end)
    local p = AGENT_VEH_TIER[vi.mk or ''] or 5
    if vi.sp then p = p + 15 end
    if vi.hp and vi.kg and vi.kg > 0 then p = p + math.min(30, 60 * vi.hp / vi.kg) end
    p = p + (AGENT_VEH_CLS[vi.cls or ''] or 0)
    if (vi.kg or 0) > 3000 or vi.rid:find('^v_utility4_') then p = p - 20 end
    vi.pr = math.max(0, math.min(100, math.floor(p + 0.5)))
    AgentVehInfoCache[sid] = vi
    if AgentVehInfoN < 300 then
        AgentVehInfoN = AgentVehInfoN + 1
        journal(string.format('VINFO %s mk=%s vt=%s hp=%s kg=%s sp=%s cls=%s hj=%s cop=%s pr=%d', sid, tostring(vi.mk),
            tostring(vi.vt), tostring(vi.hp), tostring(vi.kg), tostring(vi.sp), tostring(vi.cls), tostring(vi.hj), tostring(vi.cop), vi.pr))
    end
    return vi
end
-- occupation : occ 0 vide, 1 conducteur actif, 2 conducteur mort / inconscient, 3 passagers OU conducteur non detecte
-- (V s abstient) ; lock = etat de la portiere conducteur (1 verrouillee, 2 desactivee, 3 verrou de quete, 4 reservee)
function AgentVehOcc(ent, player, key)
    local now = os.clock()
    local c = key and AgentVehOccCache[key]
    if c and now - c.t < 1.0 then return c.o end
    local o, eid = {}, ent:GetEntityID()
    local gi = nil; pcall(function() gi = player:GetGame() end)
    local drv = nil
    pcall(function() drv = VehicleComponent.GetDriverMounted(gi, eid) end)          -- vehicleComponent.script:496
    if drv then
        o.occ = 1
        pcall(function() if not ScriptedPuppet.IsActive(drv) then o.occ = 2 end end)     -- scriptedPuppet.script:1939
        pcall(function() if VehicleComponent.IsVehicleOccupiedByHostile(eid, player) then o.hos = true end end)
        pcall(function() o.civ = (drv:IsCharacterCivilian() == true) end)            -- scriptedPuppet.script:1775
        pcall(function()                                                               -- vehicleComponent.script:1085
            local okN, nP = VehicleComponent.GetNumberOfActivePassengers(gi, eid)
            if type(nP) ~= 'number' then nP = tonumber(okN) end
            if nP and nP > 1 then o.pax = true end
        end)
    else
        pcall(function() o.occ = ent:HasPassengers() and 3 or 0 end)                 -- vehicles.script:50
    end
    pcall(function()
        local door = nil
        pcall(function() door = EVehicleDoor.seat_front_left end)
        if door == nil then door = vehicleEVehicleDoor.seat_front_left end
        local n = tonumber(EnumInt(ent:GetVehiclePS():GetDoorInteractionState(door)))   -- uint64 -> nombre (JSON)
        if n and n ~= 0 then o.lock = n end
    end)
    pcall(function() if ent:IsPrevention() then o.cop = true end end)                -- vehicles.script:35
    pcall(function() if ent:IsVehicleParked() then o.park = true end end)
    pcall(function() if ent:IsInTrafficLane() then o.lane = true end end)
    pcall(function() if ent:IsAbandoned() then o.aban = true end end)
    pcall(function() if ent:IsStolen() then o.stol = true end end)
    pcall(function() if ent:IsQuest() then o.qst = true end end)
    if key then
        AgentVehOccCache[key] = { t = now, o = o }
        if math.random() < 0.02 then for k, v in pairs(AgentVehOccCache) do if now - v.t > 30 then AgentVehOccCache[k] = nil end end end
    end
    return o
end
-- contexte de conduite : etoiles, autodrive, vehicule monte (la vitesse d un vehicule vole n est pas dans summon)
function AgentDriveCtx(inVeh)
    local o, bbs, defs, pl = {}, Game.GetBlackboardSystem(), GetAllBlackboardDefs(), Game.GetPlayer()
    pcall(function() local w = bbs:Get(defs.UI_WantedBar):GetInt(defs.UI_WantedBar.CurrentWantedLevel)
        if w and w > 0 then o.wl = w end end)
    if inVeh then
        pcall(function() local b, d = bbs:Get(defs.UI_AutodriveData), defs.UI_AutodriveData
            o.av = b:GetBool(d.AutoDriveAvailable); o.on = b:GetBool(d.AutoDriveEnabled)
            o.rem = math.floor(b:GetFloat(d.DestinationRemainingLength)) end)
        pcall(function() o.fr = bbs:Get(defs.UI_AutodriveData):GetBool(defs.UI_AutodriveData.FreeRoamEnabled) end)
        pcall(function()                                   -- 12/10 : destination REELLE de l autodrive (diagnostic)
            local a = Game.GetScriptableSystemsContainer():Get('AutoDriveSystem')
            o.dt = tonumber(EnumInt(a:GetAutodriveDestinationType()))
            local v = a:GetAutodriveDestination(); o.ax, o.ay = math.floor(v.x), math.floor(v.y)
        end)
        pcall(function() local v = pl:GetMountedVehicle()                                             -- player.script:1581
            o.rid = (TDBID.ToStringDEBUG(v:GetRecordID()):gsub('^Vehicle%.', ''))
            o.pv = v:IsPlayerVehicle()
            o.spd = math.floor(math.abs(v:GetCurrentSpeed()) * 10 + 0.5) / 10 end)
    end
    return o
end

-- 12/10 SUCCES « Methode de Stanislavski » : un choix de dialogue lie au PARCOURS DE V (LifePathBluelinePart dans la
-- legende du choix, bluelineBuilder.script:7) ; lecture seule
function AgentLifePath(c)
    local found = false
    pcall(function()
        for _, p in ipairs(c.captionParts.parts or {}) do
            local bl = nil
            pcall(function() bl = p.blueline end)
            if bl then
                for _, bp in ipairs(bl.parts or {}) do
                    local ok, isLP = pcall(function() return bp:IsA('LifePathBluelinePart') end)
                    if ok and isLP then found = true end
                end
            end
        end
    end)
    return found
end
-- 12/10 V FIGE PAR LE JEU (globales, aucune upvalue) : workspot (Locomotion natif 9), tier de scene (HighLevel 1-5), effets
-- de restriction. Valeurs natives lues dans tools/redmod/metadata.json (l ordre de psmImports.script donnerait 8).
AGENT_FRZ_TAGS = { 'NoMovement', 'NoCameraControl', 'FastForward', 'NoJump', 'NoWorldInteractions' }
function AgentFreezeInfo(player, psm, defs)
    local f = {}
    if psm then
        pcall(function() f.hl = tonumber(psm:GetInt(defs.PlayerStateMachine.HighLevel)) end)
        pcall(function() f.tier = tonumber(psm:GetInt(defs.PlayerStateMachine.SceneTier)) end)
    end
    pcall(function()
        local ws = Game.GetWorkspotSystem()
        if ws:IsActorInWorkspot(player) then
            f.ws = true
            pcall(function() local t = player:GetPlayerCurrentWorkspotTags(); if t and #t > 0 then f.wst = {}; for k = 1, math.min(#t, 4) do f.wst[k] = Game.NameToString(t[k]) end end end)
        end
    end)
    for _, tag in ipairs(AGENT_FRZ_TAGS) do
        local ok, v = pcall(function() return StatusEffectSystem.ObjectHasStatusEffectWithTag(player, CName.new(tag)) end)
        if ok and v == true then f.tags = f.tags or {}; f.tags[#f.tags + 1] = tag end
    end
    return f
end
-- V en workspot hors vehicule (les sieges de vehicule sont des workspots)
function AgentWorkspotLocked(player)
    local ok, v = pcall(function()
        local d = GetAllBlackboardDefs().PlayerStateMachine
        local psm = Game.GetBlackboardSystem():GetLocalInstanced(player:GetEntityID(), d)
        return Game.GetWorkspotSystem():IsActorInWorkspot(player) and psm:GetInt(d.Vehicle) == 0
    end)
    return ok and v == true
end
-- sortie de workspot : 1 rapide, 2 lente, 3 StopInDevice (deviceBase.script:1977)
function AgentWorkspotExit(player, step)
    local r = { step = step }
    local ws = Game.GetWorkspotSystem()
    pcall(function() r.before = ws:IsActorInWorkspot(player) end)
    if r.before == false then r.ok = true; r.reason = 'pas en workspot'; return r end
    local okX, err
    if step == 1 then
        okX, err = pcall(function() ws:SendFastExitSignal(player, Vector3.new(0, -1, 0), false, true, false, true) end)
        if not okX then okX, err = pcall(function() ws:SendFastExitSignal(player) end) end
    elseif step == 2 then
        okX, err = pcall(function() ws:SendSlowExitSignal(player) end)
    else
        okX, err = pcall(function() ws:StopInDevice(player, Vector4.new(0, 0, 0, 1), Quaternion.new(0, 0, 0, 1)) end)
        if not okX then okX, err = pcall(function() ws:StopInDevice(player) end) end
    end
    r.ok = okX
    if not okX then r.err = tostring(err) end
    return r
end

local function addProbe(name, fn) probes[#probes + 1] = { name = name, fn = fn } end

-- HUD : pertes humaines = hostile vu debout puis mort OU neutralise (assomme / vaincu)
local function hudDown(ent)
    local down = false
    pcall(function() if ent:IsDead() then down = true end end)
    if not down then pcall(function() if ent:IsIncapacitated() then down = true end end) end
    if not down then pcall(function() if ScriptedPuppet.IsDefeated(ent) then down = true end end) end
    return down
end
local function hudCountKill(key)
    if hudKilled[key] then return end
    hudKilled[key] = true
    hudKills = hudKills + 1
    hudKillsTotal = hudKillsTotal + 1
    hudEvents[#hudEvents + 1] = { os.time(), string.format('CIBLE NEUTRALISEE  (TOTAL %d)', hudKillsTotal) }
    while #hudEvents > 8 do table.remove(hudEvents, 1) end
    pcall(function() db:exec(string.format("INSERT OR REPLACE INTO hud_stats (k, v) VALUES ('kills', %d)", hudKillsTotal)) end)
end
local function hudNote(key, ent, dead)
    if hudKilled[key] then return end
    if dead or hudDown(ent) then
        if hudTrack[key] then hudCountKill(key); hudTrack[key] = nil end
    else
        local id = nil
        pcall(function() id = ent:GetEntityID() end)
        hudTrack[key] = { id = id, t = os.clock() }
    end
end
local function hudUpdateLocks(player)
    -- fil du jeu (onUpdate), a chaque image : onDraw ne fera que dessiner ces donnees
    local list = {}
    local en = lastExport and lastExport.enemies
    if hudShow and hudLocks and en and #en > 0 then
        local pos, fwd = player:GetWorldPosition(), player:GetWorldForward()
        local cam = Game.GetCameraSystem()
        for _, t in ipairs(en) do
            if not t.dead and t.x then
                local dx, dy = t.x - pos.x, t.y - pos.y
                local dist = math.sqrt(dx * dx + dy * dy)
                if dist > 0.5 and (dx * fwd.x + dy * fwd.y) / dist > 0.3 then       -- devant V (derriere, la projection est fausse)
                    local okP, sc = pcall(function() return cam:ProjectPoint(Vector4.new(t.x, t.y, (t.z or pos.z) + 0.95, 1.0)) end)
                    if okP and sc and math.abs(sc.x) <= 1.05 and math.abs(sc.y) <= 1.05 then
                        list[#list + 1] = { sx = sc.x, sy = sc.y, d = t.d or dist, police = t.police }
                    end
                end
            end
        end
        table.sort(list, function(a, b) return a.d < b.d end)
    end
    hudLockTargets = list
end
local HUD_ACC = { ['à'] = 'a', ['â'] = 'a', ['ä'] = 'a', ['é'] = 'e', ['è'] = 'e', ['ê'] = 'e', ['ë'] = 'e', ['î'] = 'i',
                  ['ï'] = 'i', ['ô'] = 'o', ['ö'] = 'o', ['ù'] = 'u', ['û'] = 'u', ['ü'] = 'u', ['ç'] = 'c', ['œ'] = 'oe',
                  ['É'] = 'E', ['È'] = 'E', ['Ê'] = 'E', ['À'] = 'A', ['Ç'] = 'C', ['’'] = "'", ['«'] = '"', ['»'] = '"' }
local function hudAscii(str)
    -- police du HUD sans accents : translitteration, puis suppression de tout octet non ASCII restant
    str = tostring(str or '')
    for k, v in pairs(HUD_ACC) do str = str:gsub(k, v) end
    str = str:gsub('[\128-\255]', '')
    return str:upper()
end
local function hudAnalyze(obj, cls, d, player)
    -- fil du jeu (tick d export) : analyse de l objet vise ; infos fixes en cache par entite
    local scan = { cls = cls, d = d }
    local key = nil
    pcall(function() key = tostring(obj:GetEntityID().hash) end)
    local c = key and hudScanCache[key]
    if not c then
        c = {}
        if cls == 'gamePuppet' then
            pcall(function() c.name = GetLocalizedText(tostring(obj:GetDisplayName())) end)
            pcall(function()
                local aff = tostring(TweakDBInterface.GetCharacterRecord(obj:GetRecordID()):Affiliation():Type())
                c.aff = aff:gsub('gamedataAffiliation : ', ''):gsub(' %(%d+%)', '')
            end)
            pcall(function() c.lvl = Game.GetStatsSystem():GetStatValue(obj:GetEntityID(), gamedataStatType.Level) end)
            pcall(function() c.police = obj:IsPolice() end)
        elseif cls == 'vehicleBaseObject' then
            pcall(function() c.name = GetLocalizedText(tostring(obj:GetDisplayName())) end)
        end
        if key then
            hudScanCache[key] = c
            hudScanN = hudScanN + 1
            if hudScanN > 200 then hudScanCache, hudScanN = {}, 0 end
        end
    end
    scan.name, scan.aff, scan.lvl, scan.police = c.name, c.aff, c.lvl, c.police
    if cls == 'gamePuppet' then
        pcall(function() scan.dead = obj:IsDead() end)
        pcall(function() scan.hp = Game.GetStatPoolsSystem():GetStatPoolValue(obj:GetEntityID(), gamedataStatPoolType.Health, true) end)
        pcall(function()
            local a = obj:GetAttitudeTowards(player)
            if a == EAIAttitude.AIA_Hostile then scan.att = 'HOSTILE'
            elseif a == EAIAttitude.AIA_Friendly then scan.att = 'AMICAL'
            else scan.att = 'NEUTRE' end
        end)
        pcall(function() scan.combat = obj:IsInCombat() end)
        if not scan.dead and hudDown(obj) then scan.down = true end
    end
    hudScan = scan
end
local function hudCheckTracked()
    -- les requetes de ciblage excluent souvent les morts : un hostile disparu est re-verifie par son entite
    local now = os.clock()
    if now - hudCheckT < 0.5 then return end
    hudCheckT = now
    local n = 0
    for key, rec in pairs(hudTrack) do
        n = n + 1
        local down = false
        pcall(function()
            local ent = rec.id and Game.FindEntityByID(rec.id)
            if ent and hudDown(ent) then down = true end
        end)
        if down then hudCountKill(key); hudTrack[key] = nil
        elseif now - rec.t > 60 then hudTrack[key] = nil end
    end
    if n > 300 then hudTrack = {} end
end

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

addProbe('GRENADE: EHotkey.RB / GetActiveGadget / GetGrenadeCharges', function()   -- 12/10 grenade adaptee
    local p = Game.GetPlayer()
    local ed = Game.GetScriptableSystemsContainer():Get('EquipmentSystem'):GetPlayerData(p)
    local rb = tostring(EHotkey.RB) .. '=' .. tostring(EnumInt(EHotkey.RB))
    local g, g2 = ed:GetActiveGadget(), ed:GetItemIDFromHotkey(EHotkey.RB)
    local same = tostring(ItemID.GetTDBID(g)) == tostring(ItemID.GetTDBID(g2))
    return rb, TDBID.ToStringDEBUG(ItemID.GetTDBID(g)) .. ' kind=' .. tostring(AgentGrenKind(ItemID.GetTDBID(g))) .. ' same=' .. tostring(same),
           'charges=' .. tostring(p:GetGrenadeCharges()) .. ' pool=' .. tostring(Game.GetStatPoolsSystem():GetStatPoolValue(p:GetEntityID(), gamedataStatPoolType.GrenadesCharges, false))
end)
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
    info.sel = breachSel                          -- derniere case selectionnee (OnPositionSelected) + compteur
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

local function qhShort(s)
    s = tostring(s or '')
    if s == '' or s == 'nil' or s:find('^userdata') then return nil end
    return (s:gsub('^QuickHack%.', ''))
end
local function qhField(q, name)
    -- 12/10 (releve QHPOPDUMP en jeu, 3.0.80) : les champs s appellent title, cost, isLocked... SANS prefixe m_
    -- (noms des scripts 2.x) ; on essaie les deux
    local v = nil
    pcall(function() v = q[name] end)
    if v == nil then pcall(function() v = q['m_' .. name] end) end
    return v
end
local function qhFromData(q, i)
    -- 24/09 : QuickhackData du panneau (title, action, cost, isLocked...). La liste en cache ne donnait que
    -- des handles illisibles (« userdata: 0x... ») : V hachait au hasard (141 « par defaut » le 23/09).
    local r = { i = i }
    local act = qhField(q, 'action')
    pcall(function() r.action = qhShort(TDBID.ToStringDEBUG(act:GetObjectActionID())) end)
    if not r.action then pcall(function() r.action = qhShort(TDBID.ToStringDEBUG(act:GetObjectActionRecord():GetID())) end) end
    pcall(function()
        local raw = tostring(qhField(q, 'title') or '')
        local t = GetLocalizedText(raw)
        if not t or t == '' then t = raw end
        if t ~= '' and t ~= 'nil' and not t:find('^ToCName') then r.title = t:sub(1, 28) end
    end)
    pcall(function() r.cost = tonumber(qhField(q, 'cost')) end)
    pcall(function() if qhField(q, 'isLocked') == true then r.locked = true end end)
    pcall(function()                                -- DeviceQuickHack / PuppetQuickHack : en combat, les hacks d appareil ne servent a rien
        local k = tostring(qhField(q, 'type') or ''):gsub('^.-: ', ''):gsub(' %(%d+%)', '')
        if k ~= '' and k ~= 'nil' then r.kind = k end
    end)
    pcall(function()
        local rs = tostring(qhField(q, 'inactiveReason') or '')
        if rs ~= '' and rs ~= 'nil' then r.why = (GetLocalizedText(rs) or rs):sub(1, 24) end
    end)
    return r
end

registerForEvent('onInit', function()
    os.remove('probe_progress.txt')
    journal('onInit ' .. os.date('%H:%M:%S'))
    -- Breach Protocol : on capture l instance du controleur natif a l ouverture (grille/sequences lisibles ensuite)
    local okO, errO = pcall(function()
        Observe('HackingMinigameGameController', 'OnInitialize', function(self)
            breachCtrl = self
            breachSel = { n = 0 }
            journal('BREACH ouvert : controleur capture')
        end)
        -- chaque case choisie par le joueur (ou par l agent) : le jeu appelle OnPositionSelected(Vector2)
        Observe('HackingMinigameGameController', 'OnPositionSelected', function(self, position)
            local x, y = nil, nil
            pcall(function() x, y = position.X, position.Y end)
            if x == nil then pcall(function() x, y = position.x, position.y end) end
            breachSel = { x = x, y = y, n = ((breachSel and breachSel.n) or 0) + 1, t = os.clock() }
            journal(string.format('BREACH selection #%d : (%s, %s)', breachSel.n, tostring(x), tostring(y)))
        end)
        Observe('HackingMinigameGameController', 'OnUninitialize', function(self)
            breachCtrl = nil
            journal('BREACH ferme')
        end)
    end)
    journal('BREACH hooks : ' .. tostring(okO) .. (okO and '' or (' ' .. tostring(errO))))
    -- 24/09 : liste des quickhacks REELLEMENT affichee (titre, action, cout RAM, verrouille), a l ouverture du panneau
    local okQ, errQ = pcall(function()
        Observe('QuickhacksListGameController', 'PopulateData', function(self, data)
            local list = {}
            pcall(function()
                for i = 1, math.min(#data, 10) do list[#list + 1] = qhFromData(data[i], i) end
            end)
            local okL = false
            for _, r in ipairs(list) do if r.action or r.title then okL = true end end
            qhPopList, qhPopT, qhPopOk = list, os.clock(), okL
            if not qhPopLogged and #list > 0 then
                qhPopLogged = true
                journal('QHPOP ' .. json.encode(list):sub(1, 900))
                pcall(function() journal('QHPOPDUMP ' .. GameDump(data[1]):gsub('%s+', ' '):sub(1, 1200)) end)
            end
        end)
    end)
    journal('QUICKHACK hook : ' .. tostring(okQ) .. (okQ and '' or (' ' .. tostring(errQ))))
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
    pcall(function()
        db:exec('CREATE TABLE IF NOT EXISTS hud (id INTEGER PRIMARY KEY, json TEXT)')
        db:exec('CREATE TABLE IF NOT EXISTS hud_stats (k TEXT PRIMARY KEY, v INTEGER)')
        for row in db:nrows("SELECT v FROM hud_stats WHERE k = 'kills'") do hudKillsTotal = tonumber(row.v) or 0 end
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
    pcall(hudUpdateLocks, player)

    -- une sonde par image, apres l'echauffement
    if not probesDone then runNextProbe() end

    -- commandes Python (chemins), des que le monde est pret.
    -- Une erreur ici est JOURNALISEE (une fois par message) : plus d echec silencieux.
    local okP, errP = pcall(pollCommands, player, dt)
    -- MORT DE V : si l agent est actif, le mod recharge lui-meme le dernier point de controle apres 6 s (le menu de
    -- mort fait exactement cela : deathMenu.script -> LoadLastCheckpoint(true)) ; Python peut etre occupe ailleurs
    pcall(function()
        local isDead = false
        pcall(function() isDead = player:IsDead() == true end)
        if isDead then
            if deadSince == nil then deadSince = os.clock(); journal('MORT de V detectee') end
            if not deadReloaded and os.clock() - deadSince > 6.0 and os.clock() - lastCmdClock < 180.0 then
                deadReloaded = true
                local okR, errR = pcall(function() Game.GetSystemRequestsHandler():LoadLastCheckpoint(true) end)
                journal('MORT : rechargement automatique du dernier point de controle -> ' .. tostring(okR) .. (okR and '' or (' ' .. tostring(errR))))
            end
        else
            deadSince, deadReloaded = nil, false
        end
    end)
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
        local isDead = false
        pcall(function() isDead = player:IsDead() == true end)
        local playerLevel = nil
        -- APPEL DE VEHICULE : etat et vehicule appele (blackboard VehicleSummonData), position et distance
        local summon, summonHash = nil, nil
        pcall(function()
            local sd = GetAllBlackboardDefs().VehicleSummonData
            local sb = Game.GetBlackboardSystem():Get(sd)
            local stt = sb:GetUint(sd.SummonState)
            summon = { state = tonumber(stt) or 0 }
            local eid = sb:GetEntityID(sd.SummonedVehicleEntityID)
            if eid and EntityID.IsDefined(eid) then
                summonHash = eid.hash
                local ent = Game.FindEntityByID(eid)
                if ent then
                    local p = ent:GetWorldPosition()
                    summon.x, summon.y, summon.z = p.x, p.y, p.z
                    summon.d = math.sqrt((p.x - pos.x) ^ 2 + (p.y - pos.y) ^ 2)
                    pcall(function() summon.name = GetLocalizedText(tostring(ent:GetDisplayName())) end)
                    pcall(function() summon.speed = math.floor(math.abs(ent:GetCurrentSpeed()) * 10 + 0.5) / 10 end)
                end
            end
        end)
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
        local cgState = -1
        pcall(function() cgState = psm and psm:GetInt(defs.PlayerStateMachine.CombatGadget) or -1 end)   -- 0 = Default (psmImports.script:687)
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
                    local okL, isLP = pcall(AgentLifePath, c)                 -- 12/10 : ligne de parcours de vie
                    if okL and isLP then inactiveList.lp = inactiveList.lp or {}; inactiveList.lp[#inactiveList.lp + 1] = #list end
                end
            end
            local lpIdx = inactiveList.lp; inactiveList.lp = nil
            dlg = { title = tostring(hubs.choiceHubs[1].title), choices = list, inactive = inactiveList, lp = lpIdx,
                    sel = ui:GetInt(defs.UIInteractions.SelectedIndex), hubs = #hubs.choiceHubs }
        end
        -- invite d interaction dans le monde ("Parler", "Ouvrir"...) : hub unique
        local inter = nil
        pcall(function()
            local hub = FromVariant(ui:GetVariant(defs.UIInteractions.InteractionChoiceHub))
            if hub and hub.choices and #hub.choices > 0 then
                local list = {}
                local off = nil                                       -- 12/10 : choix grises (choice.script:20)
                for j = 1, #hub.choices do
                    local c = hub.choices[j]
                    list[#list + 1] = GetLocalizedText(tostring(c.localizedName))
                    pcall(function()
                        if ChoiceTypeWrapper.IsType(c.type, gameinteractionsChoiceType.CheckFailed)
                           or ChoiceTypeWrapper.IsType(c.type, gameinteractionsChoiceType.Inactive) then off = off or {}; off[#off + 1] = j end
                    end)
                end
                inter = { title = tostring(hub.title), choices = list, off = off }
                pcall(function() inter.hold = hub.choices[1].isHoldAction end)
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
        local foes = nil                 -- nombre total d hostiles vivants (hors police), avant la troncature a 6
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
                        local key = nil                 -- 12/10 : etait local au bloc ci-dessous -> rec.id / hudNote recevaient nil
                        if ent then
                            local okH, h = pcall(function() return ent:GetEntityID().hash end)
                            key = okH and tostring(h) or tostring(ent)
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
                            -- 12/10 : nom affiche (journal : « hostile » au QG de Delamain engage 5 fois sans reaction, non identifie)
                            if rec.d < 40.0 then pcall(function() rec.name = GetLocalizedText(tostring(ent:GetDisplayName())) end) end
                            if rec.d < 40.0 then pcall(AgentFoeInfo, ent, rec) end   -- 12/10 grenade adaptee : kind/emp/imm/boss/net
                            -- 12/10 (Olivier : « V tire trop souvent sur un mur ») : LIGNE DE VUE oeil -> poitrine, geometrie STATIQUE
                            -- seulement (meme appel que spatialQueriesSystem.script:56), arretee 0,6 m avant la cible
                            if rec.d < 60.0 then
                                local okL, errL = pcall(function()
                                    local fx, fy, fz = pos.x, pos.y, pos.z + 1.6
                                    local dx, dy, dz = ep.x - fx, ep.y - fy, (ep.z + 1.2) - fz
                                    local len = math.sqrt(dx * dx + dy * dy + dz * dz)
                                    if len < 1.5 then rec.los = true; return end
                                    local k = (len - 0.6) / len
                                    local hit = Game.GetSpatialQueriesSystem():SyncRaycastByCollisionGroup(
                                        Vector4.new(fx, fy, fz, 1.0), Vector4.new(fx + dx * k, fy + dy * k, fz + dz * k, 1.0), CName.new('Static'), true, false)
                                    rec.los = not (hit == true)
                                end)
                                if not AgentLosLogged then
                                    AgentLosLogged = true
                                    journal(okL and ('OK   LOS : ligne de vue mesuree (' .. tostring(rec.los) .. ' a ' .. string.format('%.0f', rec.d) .. ' m)')
                                        or ('FAIL LOS : ' .. tostring(errL)))
                                end
                            end
                            pcall(function()
                                local sc = cam:ProjectPoint(Vector4.new(ep.x, ep.y, ep.z + 1.3, 1.0))
                                rec.sx, rec.sy = sc.x, sc.y
                            end)
                            pcall(function() rec.dead = ent:IsDead() end)
                            if not rec.dead then pcall(function() if hudDown(ent) then rec.down = true end end) end   -- assomme
                            pcall(hudNote, key, ent, rec.dead)
                            list[#list + 1] = rec
                        end
                    end
                    local nA, nA45, nP = 0, 0, 0
                    for _, r in ipairs(list) do
                        if not r.dead and not r.down then
                            if r.police then nP = nP + 1
                            else
                                nA = nA + 1
                                -- a < 45 m ET au meme etage (meme regle que le planificateur : |dz| < 3.5)
                                if r.d < 45 and math.abs((r.z or pos.z) - pos.z) < 3.5 then nA45 = nA45 + 1 end
                            end
                        end
                    end
                    foes = { n = nA, n45 = nA45, np = nP }
                    table.sort(list, enemyOrder)
                    while #list > 6 do table.remove(list) end
                    if #list > 0 then enemies = list end
                end
            end)
            if not okE then enemyDiag = 'erreur: ' .. tostring(errE) end
            pcall(hudCheckTracked)
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
            if not open then qhWasOpen = false; return end
            if not qhWasOpen then qhWasOpen = true; qhOpenT = os.clock() end
            qh = { open = true }
            pcall(function()
                local sel = FromVariant(bb2:GetVariant(d.quickHackDataSelected))
                if sel then
                    local r = qhFromData(sel, 0)                 -- c est un QuickhackData (pas un PlayerQuickhackData)
                    qh.sel, qh.selTitle = r.action, r.title
                    if not qh.sel then pcall(function() qh.sel = qhShort(TDBID.ToStringDEBUG(sel.actionRecord:GetID())) end) end
                end
            end)
            if qhPopOk and qhPopList and #qhPopList > 0 and qhPopT >= qhOpenT - 1.0 then
                qh.list, qh.src = qhPopList, 'panneau'
            end
            if not qh.list then pcall(function()
                local pd = defs.PlayerQuickHackData
                local bb3 = Game.GetBlackboardSystem():Get(pd)
                local lst = FromVariant(bb3:GetVariant(pd.CachedQuickHackList))
                if lst and #lst > 0 then
                    local list = {}
                    for i = 1, math.min(#lst, 12) do
                        local h = lst[i]
                        local rec = { i = i }
                        pcall(function() rec.action = qhShort(TDBID.ToStringDEBUG(h.actionRecord:GetID())) end)   -- ex. OverheatHack
                        pcall(function()
                            -- Caption() est un CName : GetLocalizedTextByKey (tostring donnerait « ToCName{ hash_lo... »)
                            local t = tostring(GetLocalizedTextByKey(h.actionRecord:ObjectActionUI():Caption()) or '')
                            if t ~= '' and t ~= 'nil' and not t:find('^ToCName') then rec.title = t:sub(1, 28) end
                        end)
                        pcall(function() rec.quality = h.quality end)
                        list[#list + 1] = rec
                    end
                    qh.list, qh.src = list, 'cache'
                    if not qhListLogged then
                        qhListLogged = true
                        journal('QHLIST ' .. json.encode(list):sub(1, 900))
                        -- les champs m_title etaient nil : on dumpe la structure reelle du 1er element
                        pcall(function() journal('QHDUMP ' .. GameDump(lst[1]):gsub('%s+', ' '):sub(1, 1200)) end)
                        pcall(function() journal('QHTYPE ' .. tostring(lst[1]) .. ' / ' .. type(lst[1])) end)
                    end
                end
            end) end
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
        local weapon, wk, wclip = nil, nil, nil
        pcall(function()
            local ts = Game.GetTransactionSystem()
            local obj = ts:GetItemInSlot(player, TweakDBID.new('AttachmentSlots.WeaponRight'))
            if obj then
                local wid = obj:GetItemID()
                wk = AgentItemKey(wid)                                                     -- 12/10 : exemplaire en main
                pcall(function() wclip = WeaponObject.GetMagazineAmmoCount(obj) end)     -- weapon.script:390
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
        local traffic = 0                                   -- vehicules d inconnus EN MOUVEMENT a < 30 m (voie occupee)
        if not inCombat and slowScan then
            pcall(function()
                if os.clock() - playerVehRecsT > 15.0 then
                    playerVehRecsT = os.clock()
                    local okU, ul = pcall(function() return Game.GetVehicleSystem():GetPlayerUnlockedVehicles() end)
                    if okU and type(ul) == 'table' then
                        local set = {}
                        for i = 1, #ul do pcall(function() set[TDBID.ToStringDEBUG(ul[i].recordID)] = true end) end
                        playerVehRecs = set
                    end
                end
                local qv = Game['TSQ_ALL;']()
                qv.maxDistance = (os.clock() - vehCallT < 45.0) and 400.0 or 150.0   -- apres un appel, sa moto peut arriver de loin
                qv.filterObjectByDistance = true
                pcall(function() qv.testedSet = TargetingSet.Complete end)
                local okV, partsV = Game.GetTargetingSystem():GetTargetParts(player, qv)
                if okV and partsV and #partsV > 0 then
                    local list, seenV, entOf, bestV = {}, {}, {}, nil      -- 12/10 : gouts de V
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
                                    -- IsPlayerVehicle() repond faux pour la voiture de V garee (17/09) : on compare le record
                                    if not rec.player then pcall(function() rec.player = (playerVehRecs[TDBID.ToStringDEBUG(ent:GetRecordID())] == true) end) end
                                    if not rec.player and summonHash ~= nil then pcall(function() rec.player = (ent:GetEntityID().hash == summonHash) end) end
                                    pcall(function() rec.speed = math.floor(math.abs(ent:GetCurrentSpeed()) * 10 + 0.5) / 10 end)
                                    if not rec.player and rec.d <= 30.0 and (rec.speed or 0) > 1.0 then traffic = traffic + 1 end
                                    rec.h = (tostring(key):gsub('ULL$', ''))       -- 12/10 : identite (vol d un vehicule precis)
                                    entOf[rec] = ent
                                    if not rec.player and rec.d <= 40.0 and (rec.speed or 0) <= 0.5 then   -- coup de coeur : gare, <= 40 m
                                        local vi = AgentVehInfo(ent)
                                        if vi and not vi.cop and vi.pr >= 40 and (bestV == nil or vi.pr > bestV._pr or (vi.pr == bestV._pr and rec.d < bestV.d)) then
                                            bestV = rec; rec._pr = vi.pr
                                        end
                                    end
                                    if rec.player or rec.d <= 25.0 then list[#list + 1] = rec end   -- les voitures des inconnus : 25 m ; la sienne : 150 m
                                end
                            end
                        end
                    end
                    table.sort(list, function(a, b)
                        local pa, pb = a.player and 0 or 1, b.player and 0 or 1
                        if pa ~= pb then return pa < pb end          -- les vehicules de V d abord (jamais evinces par 4 inconnus)
                        return a.d < b.d
                    end)
                    while #list > 4 do table.remove(list) end
                    if bestV then                                    -- 12/10 : le coup de coeur (jusqu a 60 m) en 5e
                        local found = false
                        for _, rv in ipairs(list) do if rv == bestV then found = true end end
                        if not found then list[#list + 1] = bestV end
                    end
                    for _, rv in ipairs(list) do
                        rv._pr = nil
                        local e = entOf[rv]
                        if e and not rv.player then
                            local vi = AgentVehInfo(e)
                            if vi then rv.rid, rv.mk, rv.vt, rv.pr, rv.cop = vi.rid, vi.mk, vi.vt, vi.pr, vi.cop end
                            local o = AgentVehOcc(e, player, rv.h)
                            rv.occ, rv.lock, rv.lane, rv.aban, rv.qst, rv.hos, rv.civ, rv.pax = o.occ, o.lock, o.lane, o.aban, o.qst, o.hos, o.civ, o.pax
                            if o.cop then rv.cop = true end
                        end
                    end
                    if #list > 0 then vehicles = list end
                    slowVehicles = vehicles
                    slowTraffic = traffic
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
                pcall(hudAnalyze, obj, cls, lookat.d, player)
            end
        end)
        if not lookat then hudScan = nil end
        -- PNJ NON HOSTILES proches (pour engager une conversation de sa propre initiative) :
        -- TSQ_NPC (sonde OK) a 15 m, attitude non hostile, avec leur nom affiche. 5 max.
        local npcs = nil
        if not inCombat and slowScan then
            pcall(function()
                local q = Game['TSQ_NPC;']()
                q.maxDistance = 45.0                       -- 45 m : voir les agressions autour de V (30 m ne suffisait pas)
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
                                local cop = false
                                pcall(function() cop = ent:IsPolice() end)
                                if cop or (rec.aff and (rec.aff:find('NCPD') or rec.aff:find('MaxTac'))) then
                                    AgentCopScan = math.min(AgentCopScan or 1e9, rec.d)       -- 12/10 : police avant troncature
                                end
                                list[#list + 1] = rec
                            end
                        end
                    end
                    table.sort(list, function(a, b) return a.d < b.d end)
                    AgentCopD, AgentCopScan = AgentCopScan, nil
                    -- les 5 plus proches (PNJ de quete a cote de V) restent TOUJOURS ; les combattants plus loin completent jusqu a 8
                    if #list > 8 then
                        local keep = {}
                        for i = 1, 5 do keep[#keep + 1] = list[i] end
                        for i = 6, #list do
                            if #keep >= 8 then break end
                            if list[i].incombat then keep[#keep + 1] = list[i] end
                        end
                        for i = 6, #list do
                            if #keep >= 8 then break end
                            if not list[i].incombat then keep[#keep + 1] = list[i] end
                        end
                        list = keep
                    end
                    if #list > 0 then npcs = list end
                    slowNpcs = npcs
                end
            end)
        end
        -- ECRAN DE CHARGEMENT de voyage rapide reste ouvert (ecran noir) : diagnostic
        local ftLoading = nil
        pcall(function()
            local fd = GetAllBlackboardDefs().FastTRavelSystem
            local fb = Game.GetBlackboardSystem():Get(fd)
            local started, finished = fb:GetBool(fd.FastTravelLoadingScreenStarted), fb:GetBool(fd.FastTravelLoadingScreenFinished)
            if started and not finished then ftLoading = true end
        end)
        -- MENU OUVERT (marchand, inventaire, carte...) et SCENE (V assis, dialogue) : la souris n agit plus sur la camera
        local menuOpen, inScene = nil, nil
        pcall(function()
            local ud = GetAllBlackboardDefs().UI_System
            menuOpen = Game.GetBlackboardSystem():Get(ud):GetBool(ud.IsInMenu)
        end)
        pcall(function()
            local si = Game.GetSceneSystem():GetScriptInterface()
            inScene = si:IsEntityInScene(id) or si:IsEntityInDialogue(id)
        end)
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
        if not inCombat and not slowScan then loot, vehicles, npcs, traffic = slowLoot, slowVehicles, slowNpcs, slowTraffic or 0 end
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
        if os.clock() - resT > 0.25 then
            resT = os.clock()
            local sps = Game.GetStatPoolsSystem()
            local r = {}
            for k, pool in pairs({ heal = 'HealingItemsCharges', gren = 'GrenadesCharges', proj = 'ProjectileLauncherCharges' }) do
                pcall(function() r[k] = math.floor(sps:GetStatPoolValue(id, gamedataStatPoolType[pool], false) + 0.01) end)
            end
            -- 12/10 : VRAIES charges (pool / cout de la grenade ACTIVE, player.script:7334) + famille sur la molette
            pcall(function() r.grenN = player:GetGrenadeCharges() end)
            pcall(function() r.healN = player:GetHealingItemCharges() end)
            pcall(function() r.projN = player:GetProjectileLauncherCharges() end)
            pcall(function()
                local g = Game.GetScriptableSystemsContainer():Get('EquipmentSystem'):GetPlayerData(player):GetActiveGadget()   -- :3300
                r.gk = ItemID.IsValid(g) and (AgentGrenKind(ItemID.GetTDBID(g)) or '?') or 'none'
            end)
            resCache = next(r) and r or nil
            -- journal a chaque changement (validation en jeu : le 24/09, heal=0 alors que V avait 3 soins)
            pcall(function()
                local js = resCache and json.encode(resCache) or 'nil'
                if js ~= resLogged then resLogged = js; journal('RES ' .. js) end
            end)
        end
        local ramNow = nil
        pcall(function() ramNow = math.floor(Game.GetStatPoolsSystem():GetStatPoolValue(id, gamedataStatPoolType.Memory, false) * 10 + 0.5) / 10 end)
        seq = seq + 1
        return { seq = seq, x = pos.x, y = pos.y, z = pos.z, yaw = player:GetWorldYaw(), ram = ramNow,
                 hp = hp, dead = isDead, level = playerLevel, swim = swim, oxygen = oxygen, combat = inCombat, vehicle = inVehicle, carrying = carrying, locomotion = locomotion, upperBody = upperBody, cg = cgState,
                 frz = AgentFreezeInfo(player, psm, defs), lootPanel = lootPanel, lootCount = lootCount, loot = loot, lookat = lookat, crimes = lastCrimes, vehicles = vehicles, traffic = traffic, summon = summon, buffs = buffs, phone = phone, breach = breach, weapon = weapon, wk = wk, wclip = wclip,
                 enemies = enemies, foes = foes, res = resCache, bodies = bodies, npcs = npcs, qh = qh, dialog = dlg, interact = inter, quest = quest, bd = bd, menu = menuOpen, scene = inScene, ftLoading = ftLoading, seqEnd = seq }
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
        pcall(function() data.dx = AgentDriveCtx(data.vehicle); if AgentCopD then data.dx.copd = math.floor(AgentCopD) end end)   -- 12/10
        local okE, s = pcall(json.encode, data)
        if not okE then
            if tostring(s) ~= lastStateErr then lastStateErr = tostring(s); journal('STATE encode: ' .. lastStateErr) end
            data.crimes, data.vehicles, data.npcs = nil, nil, nil      -- 12/10 : un champ fautif ne fige plus tout l export
            okE, s = pcall(json.encode, data)
            if not okE then s = nil end
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
    -- 12/10 (revue) : un champ non encodable (uint64...) ne doit plus laisser path.json vide et lever une erreur
    local okE, txt = pcall(json.encode, resp)
    if not okE then txt = json.encode({ seq = resp.seq, seqEnd = resp.seqEnd, ok = false, reason = 'encode : ' .. tostring(txt) }) end
    local f = io.open('path.json', 'w')
    if f then f:write(txt); f:flush(); f:close() end
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
    if avoid and ((pos.x - avoid.x) ^ 2 + (pos.y - avoid.y) ^ 2) < 64 then avoid = nil end   -- 12/10 : zone posee sur V lui-meme
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

local lastEffectNames = {}                   -- noms TweakDB des effets listes par `effects` (pour effect_remove x=index)
local lastAps = {}                           -- entites des points d acces du dernier access_points
local lastDoors = {}                         -- entites porte/dispositif par index de la derniere liste `doors`
local lastFastTravel = {}                    -- points de voyage rapide par index
local lastContacts = {}                      -- contacts du telephone (sms_list)
local mappinProbeDone = false                -- sonde des methodes de mappin (une fois)
local lastChoices = {}                       -- choix de reponse SMS (sms_read)
local lastVendorStock = {}                   -- ItemID par index de la derniere liste de stock marchand
-- marchand PRESENT (< 6 m) : une entree par entite, priorite a IsVendor()
local function findNearbyVendor(player, radius)
    -- le MARCHAND = parmi les PNJ a moins de 8 m, celui qui a le plus gros stock (chez le charcudoc, l infirmiere
    -- au comptoir etait prise pour le vendeur : 1 article, contre 409 pour le ripperdoc)
    local q = Game['TSQ_NPC;']()
    q.maxDistance = radius or 8.0
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

local moneyKey = nil
local function isMoney(id)
    -- 23/09 : l argent (« Eurodollars ») sortait de l inventaire comme un objet Gen_Misc ; le « vendre »
    -- (TransferItem au marchand puis GiveItem du prix) creait des eddies de rien (6864 -> 88218).
    local yes = false
    pcall(function()
        if not moneyKey then moneyKey = TDBID.ToStringDEBUG(MarketSystem.Money().id) end
        local k = TDBID.ToStringDEBUG(id.id)
        yes = (k == moneyKey) or (k == 'Items.money')
    end)
    if not yes then
        pcall(function()
            local m = MarketSystem.Money().id
            if id.id.hash == m.hash and id.id.length == m.length then yes = true end
        end)
    end
    return yes
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
                if rec.type == 'Gad_Grenade' or rec.type == 'Cyb_Launcher' then   -- 12/10 grenade adaptee
                    rec.gk = AgentGrenKind(ItemID.GetTDBID(id))
                    pcall(function() rec.active = (tostring(ItemID.GetTDBID(edata:GetActiveGadget())) == tostring(ItemID.GetTDBID(id))) end)
                end
                pcall(function() rec.quest = data:HasTag('Quest') end)
                pcall(AgentRangedInfo, player, ts, edata, data, id, rec)   -- 12/10 : fiche des armes a distance
                if not isMoney(id) then out[#out + 1] = rec end     -- l argent n est jamais un objet a vendre/demonter
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
                    rec.key = AgentItemKey(sid)
                end
                slots[#slots + 1] = rec
            end
            resp.slots = slots
        end)
        -- TOUCHES 1/2/3 : ce que chaque touche degaine VRAIMENT (GetWeaponSlotItem, zone WeaponWheel) ; la liste `slots`
        -- ci-dessus (zone Weapon) n est pas dans le meme ordre (17/09 : V degainait une matraque a 24 m)
        pcall(function()
            local hk = {}
            for n = 1, 3 do
                local rec = { key = n }
                -- zone WeaponWheel brute : GetWeaponSlotItem renvoie None pour l arme deja en main (bascule = rengainer)
                local okW, wid = pcall(function() return edata:GetItemInEquipSlot(gamedataEquipmentArea.WeaponWheel, n - 1) end)
                if not (okW and wid and ItemID.IsValid(wid)) then okW, wid = pcall(function() return edata:GetWeaponSlotItem(n) end) end
                if okW and wid and ItemID.IsValid(wid) then
                    pcall(function() rec.name = GetLocalizedTextByKey(TweakDBInterface.GetItemRecord(wid.id):DisplayName()) end)
                    pcall(function() rec.type = tostring(TweakDBInterface.GetItemRecord(wid.id):ItemType():Type()):gsub('gamedataItemType : ', ''):gsub(' %(%d+%)', '') end)
                    pcall(function() rec.dps = ts:GetItemData(player, wid):GetStatValueByType(gamedataStatType.EffectiveDPS) end)
                    rec.ik = AgentItemKey(wid)                -- 12/10 : cle d exemplaire (rec.key = numero de touche)
                end
                hk[#hk + 1] = rec
            end
            resp.hotkeys = hk
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
        pcall(function() resp.smartlink = Game.GetStatsSystem():GetStatValue(player:GetEntityID(), gamedataStatType.HasSmartLink) > 0 end)
        pcall(function() resp.active = AgentItemKey(edata:GetActiveItem(gamedataEquipmentArea.Weapon)) end)   -- eqS:3235
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
                if rec.type == 'Gad_Grenade' then rec.gk = AgentGrenKind(r.targetItem) end   -- 12/10
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
        -- 12/10 : CraftItem(target, rec, amount, ammoBulletAmount) (craftingSystem.script:626) retire les composants puis donne
        -- amount * ammoBulletAmount munitions : sans le 4e argument, 0 munition (10 lots perdus le 12/10). Il ne verifie ni
        -- composants ni plafond : tout se verifie ici, et le gain est MESURE.
        local rec = TweakDBInterface.GetItemRecord(tid)
        local ts = Game.GetTransactionSystem()
        local function cnt()
            local ok, n = pcall(function() return ts:GetItemQuantity(player, ItemID.CreateQuery(tid)) end)
            return (ok and type(n) == 'number') and n or -1
        end
        local isAmmo = false
        pcall(function() isAmmo = rec:TagsContains(CName.new('Ammo')) end)
        if not isAmmo then pcall(function() isAmmo = rec:TagsContains('Ammo') end) end
        local can = nil
        pcall(function() can = cs:CanItemBeCrafted(rec) end)
        if can == false then resp.reason = 'composants insuffisants'; journal('FAIL craft : ' .. resp.reason); return resp end
        local qty, lot = math.max(1, math.floor(tonumber(cmd.y) or 1)), 0
        if isAmmo then
            pcall(function() lot = tonumber(CraftingSystem.GetAmmoBulletAmount(ItemID.GetTDBID(tid) or tid)) or 0 end)
            if lot <= 0 then pcall(function() lot = tonumber(CraftingSystem.GetAmmoBulletAmount(tid)) or 0 end) end
            if lot <= 0 then resp.reason = 'taille de lot inconnue'; journal('FAIL craft : ' .. resp.reason); return resp end
            if cnt() > 0 then
                local mx = nil
                pcall(function() mx = tonumber(cs:GetMaxCraftingAmount(ts:GetItemData(player, ItemID.CreateQuery(tid)))) end)   -- :413 plafond + composants
                if mx ~= nil then
                    if mx <= 0 then resp.reason = 'plafond de munitions du jeu'; journal('FAIL craft : ' .. resp.reason); return resp end
                    qty = math.min(qty, mx)
                end
            end
        else
            qty = 1
        end
        local before = cnt()
        local okC, eC = pcall(function() cs:CraftItem(player, rec, qty, lot) end)
        local after = cnt()
        resp.qty, resp.lot, resp.before, resp.after = qty, lot, before, after
        if before >= 0 and after >= 0 then resp.gain = after - before end
        resp.ok = okC and (resp.gain == nil or resp.gain > 0)
        if not okC then resp.reason = 'CraftItem : ' .. tostring(eC)
        elseif not resp.ok then resp.reason = 'aucun objet recu' end
        journal(string.format('%s craft x%d lot=%d : %s -> %s', resp.ok and 'OK  ' or 'FAIL', qty, lot, tostring(before), tostring(after)))
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
        if isMoney(id) then resp.reason = 'argent : jamais vendu'; journal('FAIL sell : argent (refuse)'); return resp end
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
    elseif cmd.cmd == 'devices_near' then
        -- 12/10 succes « Surement des rats » : appareils PIRATABLES a moins de x m (leurres « Distraire les ennemis »),
        -- avec position ecran pour viser
        local q = Game['TSQ_ALL;']()
        q.maxDistance = tonumber(cmd.x) or 25.0
        q.filterObjectByDistance = true
        pcall(function() q.testedSet = TargetingSet.Complete end)
        local okT, parts = Game.GetTargetingSystem():GetTargetParts(player, q)
        local pos, cam, out, seen = player:GetWorldPosition(), Game.GetCameraSystem(), {}, {}
        if okT and parts then
            for i = 1, #parts do
                local comp = TS_TargetPartInfo.GetComponent(parts[i])
                local ent = comp and comp:GetEntity() or nil
                local okD, isD = false, false
                if ent then okD, isD = pcall(function() return ent:IsA('gameDevice') or ent:IsA('Device') end) end
                if okD and isD then
                    local key = tostring(ent)
                    pcall(function() key = tostring(ent:GetEntityID().hash) end)
                    local qh = false
                    pcall(function() qh = ent:IsQuickHackAble() end)
                    if qh and not seen[key] and #out < 12 then
                        seen[key] = true
                        local p = ent:GetWorldPosition()
                        local rec = { x = p.x, y = p.y, z = p.z, d = math.sqrt((p.x - pos.x) ^ 2 + (p.y - pos.y) ^ 2) }
                        pcall(function() rec.name = GetLocalizedText(tostring(ent:GetDisplayName())) end)
                        pcall(function() local s2 = cam:ProjectPoint(Vector4.new(p.x, p.y, p.z + 0.5, 1.0)); rec.sx, rec.sy = s2.x, s2.y end)
                        out[#out + 1] = rec
                    end
                end
            end
        end
        resp.ok, resp.devices = true, out
        return resp
    elseif cmd.cmd == 'vendor_locate' then
        -- 12/10 : le VRAI marchand dans un rayon x (le marqueur de carte est a l entree ; le charcudoc est dedans / en bas)
        local v = findNearbyVendor(player, tonumber(cmd.x) or 40.0)
        if not v then resp.reason = 'aucun marchand'; return resp end
        local isV = false
        pcall(function() isV = v:IsVendor() end)
        local p = v:GetWorldPosition()
        resp.ok, resp.x, resp.y, resp.z, resp.vendor = isV, p.x, p.y, p.z, nil
        pcall(function() resp.vendor = GetLocalizedText(tostring(v:GetDisplayName())) end)
        if not isV then resp.reason = 'pas un marchand' end
        journal(string.format('%s vendor_locate %s a (%.0f,%.0f,%.0f)', isV and 'OK  ' or 'FAIL', tostring(resp.vendor), p.x, p.y, p.z))
        return resp
    elseif cmd.cmd == 'cw_status' then
        -- 12/10 succes T-2077 : zones de cyberware (equipmentSystem.script:3357) : implants poses / emplacements ; capacite libre
        local edata = Game.GetScriptableSystemsContainer():Get('EquipmentSystem'):GetPlayerData(player)
        local areas = {}
        for _, a in ipairs({ 'SystemReplacementCW', 'FrontalCortexCW', 'EyesCW', 'MusculoskeletalSystemCW', 'NervousSystemCW',
                             'CardiovascularSystemCW', 'IntegumentarySystemCW', 'ArmsCW', 'LegsCW', 'HandsCW' }) do
            local rec = { a = a }
            pcall(function() rec.n = tonumber(edata:GetNumberOfItemsInEquipmentArea(gamedataEquipmentArea[a])) end)
            pcall(function() rec.slots = tonumber(edata:GetNumberOfSlots(gamedataEquipmentArea[a])) end)
            areas[#areas + 1] = rec
        end
        pcall(function() resp.avail = Game.GetStatsSystem():GetStatValue(player:GetEntityID(), gamedataStatType.HumanityAvailable) end)
        resp.ok, resp.areas = true, areas
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
                    if rec.type and rec.type:sub(1, 3) == 'Cyb' then           -- 12/10 : zone et cout en capacite
                        pcall(function() rec.area = AgentEnumName(EquipmentSystem.GetEquipAreaType(id)) end)
                        pcall(function() rec.cap = idata:GetStatValueByType(gamedataStatType.Humanity) end)
                    end
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
        if cmd.z ~= 1 and AgentWorkspotLocked(player) then
            resp.reason = 'V en workspot (scene) : teleportation refusee'     -- 12/10 : la teleportation cassait la scene
            journal('FAIL fast_travel : ' .. resp.reason); return resp
        end
        local fts = Game.GetScriptableSystemsContainer():Get('FastTravelSystem')
        local did, errs = {}, {}
        -- source du jeu (fastTravelSystem.script) : PerformFastTravel(player, nodeData) ne verifie que HasFastTravelPoint
        -- et destination ~= point de depart, puis TeleportToNode : utilisable de N IMPORTE OU (pas besoin d une borne)
        pcall(function()
            local bbd = GetAllBlackboardDefs().FastTRavelSystem
            local bb = Game.GetBlackboardSystem():Get(bbd)
            bb:SetVariant(bbd.StartingPoint, ToVariant(TweakDBID.new('')))   -- sinon refus si on repart du dernier point d arrivee
        end)
        -- 16/09 19:xx : PerformFastTravel appelle uiSystem.NotifyFastTravelStart() (fondu au noir / ecran de chargement gere
        -- par la carte du jeu) ; sans cette carte ouverte l ecran est reste NOIR apres le voyage. On teleporte donc
        -- directement au noeud de la borne (meme destination, pas de notification UI), le reste en repli.
        local tries = {
            { 'TeleportToNode', function() Game.GetTeleportationFacility():TeleportToNode(player, p:GetMarkerRef()) end },
            { 'PerformFastTravel(player, p)', function() fts:PerformFastTravel(player, p) end },
            { 'PerformFastTravelRequest', function()
                local req = PerformFastTravelRequest.new()
                req.pointData = p
                req.player = player
                fts:QueueRequest(req)
            end },
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
    elseif cmd.cmd == 'reload' then
        -- ECRAN DE MORT : recharger la derniere sauvegarde comme le fait le menu de mort du jeu (deathMenu.script)
        journal('RUN  reload')
        local h = nil
        pcall(function() h = Game.GetSystemRequestsHandler() end)
        if h == nil then pcall(function() h = GetSystemRequestsHandler() end) end
        if h == nil then resp.reason = 'SystemRequestsHandler indisponible'; journal('FAIL reload : ' .. resp.reason); return resp end
        local has = nil
        pcall(function() has = h:HasLastCheckpoint() end)
        local okR, errR = pcall(function() h:LoadLastCheckpoint(true) end)
        local okR2 = false
        if not okR then okR2 = pcall(function() h:LoadLastCheckpoint(false) end) end
        resp.ok, resp.hasCheckpoint = (okR or okR2) and (has ~= false), has
        if not resp.ok then resp.reason = (has == false) and 'aucun checkpoint' or ('LoadLastCheckpoint : ' .. tostring(errR)) end
        journal('OK   reload : LoadLastCheckpoint (checkpoint=' .. tostring(has) .. ', err=' .. tostring(okR and '' or errR) .. ')')
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
        local cooldown = nil
        pcall(function() cooldown = vs:IsActivePlayerVehicleOnCooldown(typeEnum) end)
        local restricted = nil
        pcall(function() restricted = VehicleSystem.IsSummoningVehiclesRestricted(GetGameInstance()) end)
        if restricted == nil then pcall(function() restricted = Game['VehicleSystem::IsSummoningVehiclesRestricted;GameInstance'](GetGameInstance()) end) end
        -- identifiant garage (le script du jeu passe recordID par cast implicite ; en Lua on construit / resout)
        local gid = nil
        pcall(function() gid = GarageVehicleID.Resolve(TDBID.ToStringDEBUG(pick.v.recordID)) end)
        if gid == nil then pcall(function() gid = GarageVehicleID.new({ recordID = pick.v.recordID }) end) end
        local toggled = false
        if gid ~= nil then toggled = pcall(function() vs:TogglePlayerActiveVehicle(gid, typeEnum, true) end) end
        if not toggled then toggled = pcall(function() vs:TogglePlayerActiveVehicle(pick.v.recordID, typeEnum, true) end) end
        if not toggled then toggled = pcall(function() vs:TogglePlayerActiveVehicle(pick.v, typeEnum, true) end) end
        if toggled then did[#did + 1] = 'TogglePlayerActiveVehicle' end
        -- un vehicule de V deja exporte a moins de 150 m : inutile de le retirer / rappeler, V le rejoint
        if slowVehicles then
            for _, sv in ipairs(slowVehicles) do
                if sv.player and (sv.d or 999) <= 150.0 then
                    resp.ok, resp.spawned, resp.existing, resp.name, resp.vtype, resp.dist, resp.total = true, true, true, sv.name or pick.name, pick.vtype, sv.d, #list
                    journal(string.format('OK   vehicle_call : deja present %s a %.0f m', tostring(sv.name), sv.d))
                    return resp
                end
            end
        end
        -- (plus de Despawn prealable : apres un Despawn, SpawnPlayerVehicle repondait false a chaque fois ; le seul appel
        --  reussi etait sans. Le jeu fait venir / teleporte lui-meme l exemplaire existant.)
        -- restrictions : tableau de CName -> chaines (un CName brut fait echouer l encodage JSON de TOUTE la reponse :
        -- « mod muet » a chaque appel de vehicule depuis le 16/09 20:52)
        pcall(function()
            local rl, out = vs:GetVehicleRestrictions(), {}
            for i = 1, #rl do
                local ok1, s1 = pcall(function() return Game.NameToString(rl[i]) end)
                out[#out + 1] = ok1 and s1 or tostring(rl[i])
            end
            resp.restrictions = out
        end)
        pcall(function() resp.vcooldown = vs:IsPlayerVehicleOnCooldown(typeEnum, pick.v.recordID) end)
        vehCallT = os.clock()
        -- 24/09 (Olivier : « au moins trois vehicules apparaissent en meme temps, d ou un embouteillage ») : les trois
        -- methodes etaient appelees a la suite -- SpawnPlayerVehicle renvoyait false mais faisait apparaitre le vehicule
        -- quand meme (journal : route=false, false, puis SpawnActivePlayerVehicle=true). UNE seule methode : celle du
        -- jeu (vehicule actif, choisi juste avant par TogglePlayerActiveVehicle) ; l autre seulement si elle n existe pas.
        local spawned = false
        local okA, rA = pcall(function() return vs:SpawnActivePlayerVehicle(typeEnum) end)
        if okA then did[#did + 1] = 'SpawnActivePlayerVehicle=' .. tostring(rA); spawned = (rA ~= false) end
        if not okA then
            local okS, rS = pcall(function() return vs:SpawnPlayerVehicle(typeEnum, pick.v.recordID, true) end)   -- sur une VOIE valide
            if okS then did[#did + 1] = 'SpawnPlayerVehicle(route)=' .. tostring(rS); spawned = (rS ~= false) end
        end
        resp.ok, resp.name, resp.vtype, resp.methodes, resp.total = (#did > 0), pick.name, pick.vtype, did, #list
        resp.spawned, resp.cooldown, resp.restricted = spawned, cooldown, restricted
        if not spawned then resp.reason = 'spawn refuse (cooldown=' .. tostring(cooldown) .. ', restriction=' .. tostring(restricted) .. ')' end
        journal(string.format('OK   vehicle_call : %s (%s) parmi %d [%s] spawn=%s cooldown=%s restriction=%s', tostring(pick.name), tostring(pick.vtype), #list, table.concat(did, ','), tostring(spawned), tostring(cooldown), tostring(restricted)))
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
    elseif cmd.cmd == 'weapon_slot' then
        -- DEGAINER l arme de la touche x (1..3 ; 0 = rengainer) par la requete du jeu, le chemin exact des touches
        -- (defaultTransition.SendEquipmentSystemWeaponManipulationRequest) : sans clavier, sans dependre du focus
        local n = math.floor(tonumber(cmd.x) or 0)
        local names = { [0] = 'UnequipWeapon', [1] = 'RequestWeaponSlot1', [2] = 'RequestWeaponSlot2', [3] = 'RequestWeaponSlot3' }
        if not names[n] then resp.reason = 'touche inconnue'; return resp end
        local okE, errE = pcall(function()
            local req = EquipmentSystemWeaponManipulationRequest.new()
            req.owner = player
            req.requestType = EquipmentManipulationAction[names[n]]
            Game.GetScriptableSystemsContainer():Get('EquipmentSystem'):QueueRequest(req)
        end)
        resp.ok = okE
        if not okE then resp.reason = 'requete refusee : ' .. tostring(errE) end
        journal((okE and 'OK   ' or 'FAIL ') .. 'weapon_slot ' .. tostring(n) .. (okE and '' or (' : ' .. tostring(errE))))
        return resp
    elseif cmd.cmd == 'wslots' then
        -- 12/10 : leger (sans GetItemList) : 3 emplacements d arme, arme active de la zone, arme en main
        local edata = Game.GetScriptableSystemsContainer():Get('EquipmentSystem'):GetPlayerData(player)
        local slots = {}
        for i = 0, 2 do
            local rec = { slot = i + 1 }
            pcall(function()
                local sid = edata:GetItemInEquipSlot(gamedataEquipmentArea.Weapon, i)
                rec.key = AgentItemKey(sid)
                if rec.key then rec.name = GetLocalizedTextByKey(TweakDBInterface.GetItemRecord(ItemID.GetTDBID(sid)):DisplayName()) end
            end)
            -- roue (ce que la touche i+1 degaine) : copie de la zone Weapon par UpdateWeaponWheel (eqS:3972, appele en fin d EquipItem :821)
            pcall(function() rec.wkey = AgentItemKey(edata:GetItemInEquipSlot(gamedataEquipmentArea.WeaponWheel, i)) end)
            slots[#slots + 1] = rec
        end
        pcall(function() resp.active = AgentItemKey(edata:GetActiveItem(gamedataEquipmentArea.Weapon)) end)
        pcall(function() resp.held = AgentItemKey(Game.GetTransactionSystem():GetItemInSlot(player, TweakDBID.new('AttachmentSlots.WeaponRight')):GetItemID()) end)
        resp.ok, resp.slots = true, slots
        return resp
    elseif cmd.cmd == 'equip_ws' then
        -- 12/10 : range une arme dans un emplacement d arme NON actif (branche C de EquipItem, eqS:780-793 : UnequipItem
        -- de l ancienne, ecriture, UpdateWeaponWheel ; sans animation). hash = hash TweakDB, z = rng_seed, y = emplacement
        -- 1..3, x = index de la derniere liste d inventaire (repli). Chemin du menu : GameplayEquipRequest (eqS:6530).
        local slot = math.floor(tonumber(cmd.y) or 3)
        local wantH, wantS = tonumber(cmd.hash), tonumber(cmd.z) or 0
        if slot < 1 or slot > 3 or not wantH then resp.reason = 'parametres : hash, z, y=1..3'; return resp end
        local ts = Game.GetTransactionSystem()
        local es = Game.GetScriptableSystemsContainer():Get('EquipmentSystem')
        local edata = es:GetPlayerData(player)
        local id = nil
        local okL, items = ts:GetItemList(player)
        if type(okL) == 'table' then items = okL end
        for i = 1, #(items or {}) do
            local okI, iid = pcall(function() return items[i]:GetID() end)
            if okI then
                local _, h, sd = AgentItemKey(iid)
                if h == wantH and (wantS == 0 or sd == wantS) then id = iid; break end
            end
        end
        -- PAS de repli sur un index de liste : l arme a pu etre vendue / demontee, l index designerait un autre objet (revue)
        if not id then resp.reason, resp.absent = 'arme absente du sac', true; journal('FAIL equip_ws : absente'); return resp end
        local before, held = {}, nil
        for i = 0, 2 do before[i + 1] = ''; pcall(function() before[i + 1] = AgentItemKey(edata:GetItemInEquipSlot(gamedataEquipmentArea.Weapon, i)) or '' end) end
        pcall(function() held = AgentItemKey(ts:GetItemInSlot(player, TweakDBID.new('AttachmentSlots.WeaponRight')):GetItemID()) end)
        local key = AgentItemKey(id)
        resp.before, resp.held, resp.key = before, held, key
        if before[slot] == key then resp.ok, resp.already = true, true; return resp end
        if held and held == before[slot] then
            resp.reason = 'emplacement en main (echange en main non sonde)'; journal('FAIL equip_ws : ' .. resp.reason); return resp
        end
        local okQ, errQ = pcall(function()
            for i = 1, 3 do
                if i ~= slot and before[i] == key then
                    local u = UnequipRequest.new()                     -- eqS:6580
                    u.owner = player; u.areaType = gamedataEquipmentArea.Weapon; u.slotIndex = i - 1; u.force = true
                    es:QueueRequest(u)
                end
            end
            local r = GameplayEquipRequest.new()
            r.owner = player; r.itemID = id; r.slotIndex = slot - 1
            r.addToInventory = false
            r.blockUpdateWeaponActiveSlots = true                       -- jamais la branche A (eqS:767 : degainer si activeIndex == slot)
            r.forceEquipWeapon = false                                  -- branche C (eqS:780-793), PAS la branche B
            es:QueueRequest(r)
        end)
        resp.ok = okQ
        if not okQ then resp.reason = 'requete refusee : ' .. tostring(errQ) end
        journal((okQ and 'OK   ' or 'FAIL ') .. 'equip_ws ' .. tostring(key) .. ' -> ' .. slot .. ' (avant ' .. table.concat(before, ' | ') .. ', en main ' .. tostring(held) .. ')' .. (okQ and '' or (' err=' .. tostring(errQ))))
        return resp
    elseif cmd.cmd == 'grenade_select' then
        -- 12/10 GRENADE ADAPTEE : met sur le raccourci RB (clic molette = GetActiveGadget) la meilleure grenade de la famille x.
        -- x : 1 frag, 2 emp, 3 fire, 4 bio, 5 flash, 6 smoke, 7 cut, 8 recon ; 0 = lister seulement
        local fams = { 'frag', 'emp', 'fire', 'bio', 'flash', 'smoke', 'cut', 'recon' }
        local want = fams[math.floor(tonumber(cmd.x) or 0)]
        journal('RUN  grenade_select ' .. tostring(want or 'liste'))
        local ts = Game.GetTransactionSystem()
        local es = Game.GetScriptableSystemsContainer():Get('EquipmentSystem')
        local edata = es:GetPlayerData(player)
        local function active()
            local s1, k1 = 'none', 'none'
            pcall(function()
                local g = edata:GetActiveGadget()                                                -- equipmentSystem.script:3300
                if ItemID.IsValid(g) then s1 = tostring(ItemID.GetTDBID(g)); k1 = AgentGrenKind(ItemID.GetTDBID(g)) or '?' end
            end)
            return s1, k1
        end
        local prevS, prevK = active()
        resp.prevk = prevK
        local cg = 0
        pcall(function()
            local d = GetAllBlackboardDefs().PlayerStateMachine
            cg = Game.GetBlackboardSystem():GetLocalInstanced(player:GetEntityID(), d):GetInt(d.CombatGadget)   -- blackboardDefinitions.script:95
        end)
        resp.cg = cg
        local okL, items = ts:GetItemList(player)
        if type(okL) == 'table' then items = okL end
        local have, best, bestQ = {}, nil, -1
        for i = 1, #(items or {}) do
            pcall(function()
                local iid = items[i]:GetID()
                local tid = ItemID.GetTDBID(iid)
                local k = AgentGrenKind(tid)
                if k and k ~= 'launcher' and k ~= 'ability' then
                    local q = TweakDBInterface.GetItemRecord(tid):Quality():Value()                -- tweakDBRecords.script:7248
                    have[#have + 1] = k .. ':' .. q
                    if k == want and q > bestQ and ts:GetItemQuantity(player, iid) > 0 then best, bestQ = iid, q end   -- transactionSystem.script:41
                end
            end)
        end
        resp.have = table.concat(have, ',')
        if not want then resp.ok = true; journal('OK   grenade_select liste [' .. resp.have .. '] active=' .. prevK); return resp end
        if not best then resp.reason = 'aucune grenade ' .. want; journal('FAIL grenade_select ' .. want .. ' absente [' .. resp.have .. ']'); return resp end
        local bestS = tostring(ItemID.GetTDBID(best))
        if prevS == bestS then resp.ok, resp.method = true, 'deja active'; return resp end
        if prevK == 'launcher' then resp.reason = 'lance-projectiles sur la molette'; journal('FAIL grenade_select : RB = lance-projectiles, inchange'); return resp end
        if cg ~= 0 then resp.reason = 'lancer en cours (cg=' .. tostring(cg) .. ')'; journal('FAIL grenade_select : ' .. resp.reason); return resp end
        local RB = nil
        pcall(function() RB = EHotkey.RB end)
        if RB == nil then pcall(function() RB = Enum.new('EHotkey', 'RB') end) end              -- repli [NON VERIFIE]
        local method, errA = 'AssignItemToHotkey', nil
        if RB ~= nil then
            local okA, e = pcall(function() edata:AssignItemToHotkey(best, RB) end)            -- :2176, synchrone
            if not okA then errA = tostring(e) end
        else errA = 'EHotkey.RB introuvable' end
        if active() ~= bestS and RB ~= nil then
            method = 'HotkeyAssignmentRequest/Cycle'                                          -- :87 ; Cycle = pas de tutoriel ; traite au tick suivant
            local okQ, eQ = pcall(function()
                es:QueueRequest(HotkeyAssignmentRequest.Construct(best, RB, player, EHotkeyRequestType.Cycle))
            end)
            resp.pending = okQ
            if not okQ then errA = tostring(errA) .. ' / ' .. tostring(eQ) end
        end
        local nowS, nowK = active()
        resp.ok, resp.method, resp.nowk = (nowS == bestS), method, nowK
        pcall(function() resp.charges = player:GetGrenadeCharges() end)                       -- player.script:7334
        journal(string.format('%s grenade_select %s -> %s (%s) RB=%s charges=%s%s',
            resp.ok and 'OK  ' or (resp.pending and 'WAIT' or 'FAIL'), prevK, want, method, nowK,
            tostring(resp.charges), errA and (' err=' .. errA) or ''))
        return resp
    elseif cmd.cmd == 'ws_exit' then
        local r = AgentWorkspotExit(player, math.floor(tonumber(cmd.x) or 1))
        for k, v in pairs(r) do resp[k] = v end
        journal(string.format('%s ws_exit %d : avant=%s %s', r.ok and 'OK  ' or 'FAIL', r.step, tostring(r.before), tostring(r.err or r.reason or '')))
        return resp
    elseif cmd.cmd == 'effects' then
        -- DIAGNOSTIC : effets de statut appliques a V (restrictions de gameplay incluses : « Action impossible »)
        local out, restr = {}, {}
        lastEffectNames = {}
        pcall(function()
            local list = Game.GetStatusEffectSystem():GetAppliedEffects(player:GetEntityID())
            for i = 1, #list do
                local e = list[i]
                local name, tags = '?', {}
                pcall(function() name = TDBID.ToStringDEBUG(e:GetRecord():GetID()) end)
                pcall(function()
                    local t = e:GetRecord():GameplayTags()
                    for j = 1, #t do tags[#tags + 1] = Game.NameToString(t[j]) end
                end)
                local rem = nil
                pcall(function() rem = e:GetRemainingDuration() end)
                out[#out + 1] = { i = i, name = name, tags = tags, remaining = rem }
                lastEffectNames[i] = name
            end
        end)
        for _, tag in ipairs({ 'NoCombat', 'NoWeapons', 'NoMovement', 'NoJump', 'NoScanning', 'NoQuickHacks', 'InScene', 'NoDriving',
                               'NoPhone', 'PhoneCall', 'NoCameraControl', 'NoZooming', 'NoDodge', 'NoSprint', 'FistFight', 'VehicleNoSummoning',
                               'NoCrouch', 'NoWorldInteractions', 'NoInteractions', 'Braindance', 'Restricted', 'VehicleNoInteraction', 'NoAiming' }) do
            local ok, v = pcall(function() return StatusEffectSystem.ObjectHasStatusEffectWithTag(player, CName.new(tag)) end)
            if ok and v == true then restr[#restr + 1] = tag end
        end
        local inScene = nil
        pcall(function() inScene = Game.GetSceneSystem():GetScriptInterface():IsEntityInScene(player:GetEntityID()) end)
        resp.ok, resp.effects, resp.restrictions, resp.inScene = true, out, restr, inScene
        journal(string.format('OK   effects : %d effet(s), restrictions [%s], scene=%s', #out, table.concat(restr, ','), tostring(inScene)))
        return resp
    elseif cmd.cmd == 'effect_remove' then
        -- retirer un effet de statut : x = index dans la derniere liste `effects`
        local name = lastEffectNames[math.floor(tonumber(cmd.x) or -1)]
        if not name then resp.reason = 'index inconnu (refaire effects)'; return resp end
        local okR, errR = pcall(function() Game.GetStatusEffectSystem():RemoveStatusEffect(player:GetEntityID(), TweakDBID.new(name)) end)
        resp.ok = okR
        if not okR then resp.reason = tostring(errR) end
        journal((okR and 'OK   ' or 'FAIL ') .. 'effect_remove ' .. name .. (okR and '' or (' : ' .. tostring(errR))))
        return resp
    elseif cmd.cmd == 'autodrive' then
        -- 12/10 : l autodrive ordinaire refuse un vehicule qui n est pas a V (autoDriveSystem.script:170) ; le mode Delamain
        -- saute ce test (:331). x=2 sonde, x=1 demarrage, x=0 arret.
        journal('RUN  autodrive ' .. tostring(cmd.x))
        local ads = Game.GetScriptableSystemsContainer():Get('AutoDriveSystem')
        if not ads then resp.reason = 'AutoDriveSystem introuvable'; journal('FAIL autodrive : ' .. resp.reason); return resp end
        pcall(function() resp.avail = ads:GetAutodriveAvailable() end)
        pcall(function() resp.lane = tonumber(EnumInt(ads:CheckCurrentLaneValidity())) end)      -- 0 voie valide, 1 voie non valide, 2 hors route
        pcall(function() resp.dest = tonumber(EnumInt(ads:GetAutodriveDestinationType())) end)   -- 0 aucune, 1 marqueur du joueur, 2 quete
        pcall(function() resp.on = ads:GetAutodriveEnabled() end)
        pcall(function() resp.pv = player:GetMountedVehicle():IsPlayerVehicle() end)
        if cmd.x == 1 then
            local okR, eR = pcall(function() local q = EnableAutoDriveRequest.new(); q.isDelamain = true; ads:QueueRequest(q) end)
            if not okR then okR, eR = pcall(function() resp.direct = ads:SetAutodriveEnabled(true, true) end) end   -- natif :134
            resp.sent = okR; if not okR then resp.reason = tostring(eR) end
        elseif cmd.x == 3 then                                -- 12/10 : autodrive NORMAL (voiture de V) sans bascule G
            local okR, eR = pcall(function() local q = EnableAutoDriveRequest.new(); q.isDelamain = false; ads:QueueRequest(q) end)
            resp.sent = okR; if not okR then resp.reason = tostring(eR) end
        elseif cmd.x == 0 then
            pcall(function() ads:QueueRequest(DisableAutoDriveRequest.new()) end)
        end
        resp.ok = true
        journal(string.format('OK   autodrive x=%s avail=%s lane=%s dest=%s on=%s pv=%s sent=%s', tostring(cmd.x), tostring(resp.avail),
            tostring(resp.lane), tostring(resp.dest), tostring(resp.on), tostring(resp.pv), tostring(resp.sent)))
        return resp
    elseif cmd.cmd == 'mount' then
        -- MONTER dans un vehicule PAR SCRIPT (MountingFacility) : le vehicule appele (VehicleSummonData) s il est a < 12 m,
        -- sinon le vehicule de V le plus proche a < 8 m. Siege conducteur. Evite l invite « Enfourcher » capricieuse.
        journal('RUN  mount')
        local pos = player:GetWorldPosition()
        local target, how = nil, ''
        -- 12/10 : x = 2 -> le vehicule PRECIS en (y, z) (vol choisi par gout) : jamais le vehicule appele (cas Galena)
        local aimX, aimY = nil, nil
        if cmd.x == 2 then aimX, aimY = tonumber(cmd.y), tonumber(cmd.z) end
        if not aimX then pcall(function()
            local sd = GetAllBlackboardDefs().VehicleSummonData
            local eid = Game.GetBlackboardSystem():Get(sd):GetEntityID(sd.SummonedVehicleEntityID)
            if eid and EntityID.IsDefined(eid) then
                local ent = Game.FindEntityByID(eid)
                if ent then
                    local p = ent:GetWorldPosition()
                    if math.sqrt((p.x - pos.x) ^ 2 + (p.y - pos.y) ^ 2) <= 12.0 then target, how = ent, 'appele' end
                end
            end
        end) end
        if not target then
            pcall(function()
                local q = Game['TSQ_ALL;']()
                q.maxDistance = aimX and 10.0 or 8.0
                q.filterObjectByDistance = true
                pcall(function() q.testedSet = TargetingSet.Complete end)
                local okT, parts = Game.GetTargetingSystem():GetTargetParts(player, q)
                local best, bestD = nil, 1e9
                if okT and parts then
                    for i = 1, #parts do
                        local comp = TS_TargetPartInfo.GetComponent(parts[i])
                        local ent = comp and comp:GetEntity() or nil
                        if ent then
                            local okV, isV = pcall(function() return ent:IsA('vehicleBaseObject') end)
                            if okV and isV then
                                local p = ent:GetWorldPosition()
                                local d = math.sqrt((p.x - pos.x) ^ 2 + (p.y - pos.y) ^ 2)
                                local mine = false
                                pcall(function() mine = ent:IsPlayerVehicle() end)
                                if not mine then pcall(function() mine = (playerVehRecs[TDBID.ToStringDEBUG(ent:GetRecordID())] == true) end) end
                                if aimX then                                -- 12/10 : le vehicule vise (a 4 m pres de sa position exportee)
                                    local da = math.sqrt((p.x - aimX) ^ 2 + (p.y - aimY) ^ 2)
                                    if da <= 4.0 and da < bestD then best, bestD = ent, da end
                                elseif d < bestD and (mine or (cmd.x == 1)) then best, bestD = ent, d end   -- x = 1 : n importe quel vehicule (vol)
                            end
                        end
                    end
                end
                if best then target, how = best, aimX and 'vise' or 'proche' end
            end)
        end
        if not target then resp.reason = 'aucun vehicule a portee'; journal('FAIL mount : ' .. resp.reason); return resp end
        local okM, errM = pcall(function()
            local data = MountEventData.new()
            data.isInstant = false
            data.slotName = CName.new('seat_front_left')
            data.mountParentEntityId = target:GetEntityID()
            data.entryAnimName = CName.new('forcedTransition')
            -- structures = valeurs : on remplit des copies locales puis on les affecte (une affectation imbriquee serait perdue)
            local slot = MountingSlotId.new()
            slot.id = CName.new('seat_front_left')
            local info = MountingInfo.new()
            info.childId = player:GetEntityID()
            info.parentId = target:GetEntityID()
            info.slotId = slot
            local req = MountingRequest.new()
            req.lowLevelMountingInfo = info
            req.preservePositionAfterMounting = false
            req.mountData = data
            Game.GetMountingFacility():Mount(req)
        end)
        local tMine = (how == 'appele')                         -- 12/10 : pas de Toggle sur un vehicule qui n est pas a V
        pcall(function() tMine = tMine or target:IsPlayerVehicle() end)
        if not tMine then pcall(function() tMine = (playerVehRecs[TDBID.ToStringDEBUG(target:GetRecordID())] == true) end) end
        resp.mine = tMine
        if okM and tMine then
            -- Mount() assoit V dans le siege mais ne declare pas le vehicule comme « actif » pour le joueur :
            -- SANS CET APPEL, l autodrive n a pas de vehicule possede a router (7 echecs sur 7 le 19/09 : la
            -- voiture roulait parfois, mais jamais vers la destination -- exactement le symptome d un vehicule
            -- non reconnu comme « actif »). Meme appel que vehicle_call / quickSlotsManager.SetActiveVehicle.
            pcall(function()
                local vs = Game.GetVehicleSystem()
                local recordID = target:GetRecordID()
                local typeEnum = gamedataVehicleType.Car
                pcall(function()
                    local vtype = tostring(TweakDBInterface.GetVehicleRecord(recordID):Type():Type())
                    if vtype:find('Bike') then typeEnum = gamedataVehicleType.Bike end
                end)
                local gid = nil
                pcall(function() gid = GarageVehicleID.Resolve(TDBID.ToStringDEBUG(recordID)) end)
                if gid == nil then pcall(function() gid = GarageVehicleID.new({ recordID = recordID }) end) end
                local toggled = false
                if gid ~= nil then toggled = pcall(function() vs:TogglePlayerActiveVehicle(gid, typeEnum, true) end) end
                if not toggled then pcall(function() vs:TogglePlayerActiveVehicle(recordID, typeEnum, true) end) end
            end)
        end
        resp.ok, resp.how = okM, how
        if not okM then resp.reason = 'Mount : ' .. tostring(errM) end
        journal((okM and 'OK   ' or 'FAIL ') .. 'mount (' .. how .. ')' .. (okM and '' or (' : ' .. tostring(errM))))
        return resp
    elseif cmd.cmd == 'hud_toggle' then
        hudShow = not hudShow
        resp.ok, resp.hud = true, hudShow
        return resp
    elseif cmd.cmd == 'get_directive' then
        resp.ok = true
        resp.text = pendingDirective or ''
        pendingDirective = nil
        return resp
    elseif cmd.cmd == 'access_points' then
        -- POINTS D ACCES (terminaux du Breach Protocol) a < 40 m, avec leur etat pirate
        journal('RUN  access_points')
        local q = Game['TSQ_ALL;']()
        q.maxDistance = 40.0
        q.filterObjectByDistance = true
        pcall(function() q.testedSet = TargetingSet.Complete end)
        local okT, parts = Game.GetTargetingSystem():GetTargetParts(player, q)
        local pos = player:GetWorldPosition()
        local out, seenEnt = {}, {}
        lastAps = {}
        if okT and parts then
            for i = 1, #parts do
                local comp = TS_TargetPartInfo.GetComponent(parts[i])
                local ent = comp and comp:GetEntity() or nil
                if ent then
                    local okH, h = pcall(function() return ent:GetEntityID().hash end)
                    local key = okH and tostring(h) or tostring(ent)
                    local okA, isAp = pcall(function() return ent:IsA('AccessPoint') end)
                    if not seenEnt[key] and okA and isAp then
                        seenEnt[key] = true
                        local wp = ent:GetWorldPosition()
                        local rec = { x = wp.x, y = wp.y, z = wp.z, d = math.sqrt((wp.x - pos.x) ^ 2 + (wp.y - pos.y) ^ 2), breached = false }
                        pcall(function() rec.name = GetLocalizedText(tostring(ent:GetDisplayName())) end)
                        pcall(function() rec.breached = ent:GetDevicePS():IsBreached() == true end)
                        pcall(function() rec.on = ent:GetDevicePS():IsON() end)
                        out[#out + 1] = rec
                        rec.i = #out
                        pcall(function() lastAps[#out] = ent:GetEntityID() end)
                    end
                end
            end
        end
        table.sort(out, function(a, b) return a.d < b.d end)     -- rec.i reste l index dans lastAps
        resp.ok, resp.points = true, out
        journal(string.format('OK   access_points : %d point(s) d acces', #out))
        return resp
    elseif cmd.cmd == 'jack_in' then
        -- CONNEXION A UN POINT D ACCES PAR SCRIPT : l action ToggleNetrunnerDive (celle du Breach a distance) executee
        -- sur le DevicePS ouvre le mini-jeu sans l animation de branchement (scriptableDeviceBasePS.OnActionRemoteBreach)
        local eid = lastAps[cmd.x or -1]
        if not eid then resp.reason = 'index inconnu (refaire access_points)'; return resp end
        local ent = nil
        pcall(function() ent = Game.FindEntityByID(eid) end)
        if not ent then resp.reason = 'point d acces decharge'; return resp end
        journal('RUN  jack_in ' .. tostring(cmd.x))
        local ps = nil
        pcall(function() ps = ent:GetDevicePS() end)
        local did = {}
        local okE, errE = pcall(function()
            local act = ToggleNetrunnerDive.new()
            act:SetProperties(false, false, 1, true)
            pcall(function() act:SetUp(ps) end)
            pcall(function() act:SetExecutor(player) end)
            local ok1 = pcall(function() ps:ExecutePSAction(act, player) end)
            if ok1 then did[#did + 1] = 'ExecutePSAction(act, player)' else
                local ok2 = pcall(function() ps:ExecutePSAction(act) end)
                if ok2 then did[#did + 1] = 'ExecutePSAction(act)' else
                    if pcall(function() ps:OnToggleNetrunnerDive(act) end) then did[#did + 1] = 'OnToggleNetrunnerDive' end
                end
            end
        end)
        if not okE then did[#did + 1] = 'erreur action : ' .. tostring(errE) end
        if #did == 0 or did[1]:find('erreur') then
            if pcall(function() ent:PerformDive(1, true) end) then did[#did + 1] = 'PerformDive' end
        end
        resp.ok, resp.methodes = (#did > 0), did
        if not resp.ok then resp.reason = 'aucune methode acceptee' end
        journal('OK   jack_in : ' .. table.concat(did, ','))
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
                    -- 19/10 : entree de journal du marqueur (comme la carte : worldMap.script:1613) -> V peut faire SUIVRE
                    -- cette quete par le jeu, et l autodrive a enfin une destination (sinon roue libre, V redescend)
                    pcall(function() local h = m:GetJournalPathHash(); if h and h ~= 0 then rec.jph = h end end)
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
            lastCmdClock = os.clock()                       -- l agent Python est actif
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
-- Ecrits dans agent_config.json (dossier du mod). 23/09 : seuls les reglages MODIFIES ici sont ecrits, horodates
-- (_changed_at) ; cote Python, chaque reglage prend la valeur modifiee en dernier (ici ou dans le panneau Windows).
-- L ancien panneau reecrivait TOUT (defauts compris) et ecrasait en silence le panneau Windows, modele compris.
local ui = { open = false, provider = 1, key = '', model = 'llama3.2:latest', openai_model = 'gpt-4o-mini',
             anthropic_model = 'claude-haiku-4-5-20251001', minutes = 20, saved = '',
             radio = true, driving = true, rescue = true, sell = true, ripperdoc = true, buffs = true,
             stealth = true, fasttravel = true, phone = true, sms = true, appearance = true, recipes = true, sprint = true, steal = true, terminals = true, courage = 3, style = 1, aggro = 2,
             directive = '', directiveSent = '' }
local uiCourage = { 'prudent', 'equilibre', 'temeraire' }
local uiStyle = { 'melee', 'mixte', 'distance' }
local uiAggro = { 'defensif', 'normal', 'chasseur' }
local uiProviders = { 'ollama', 'openai', 'anthropic' }
local UI_FEATS = { 'radio', 'driving', 'rescue', 'sell', 'ripperdoc', 'buffs', 'stealth', 'fasttravel', 'phone', 'sms', 'appearance', 'recipes', 'sprint', 'steal', 'terminals' }
local UI_KEYS = { 'provider', 'api_key', 'model', 'openai_model', 'anthropic_model', 'minutes', 'courage', 'style', 'aggro' }
local uiCarry, uiStamp, uiSnap = {}, {}, {}   -- reglages fixes en jeu (conserves), leurs horodatages, valeurs au chargement
local function uiValues()
    local v = { provider = uiProviders[ui.provider], api_key = ui.key, model = ui.model, openai_model = ui.openai_model,
                anthropic_model = ui.anthropic_model, minutes = ui.minutes,
                courage = uiCourage[ui.courage], style = uiStyle[ui.style], aggro = uiAggro[ui.aggro] }
    for _, k in ipairs(UI_FEATS) do v['features.' .. k] = ui[k] end
    return v
end
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
        for _, k in ipairs({ 'radio', 'driving', 'rescue', 'sell', 'ripperdoc', 'buffs', 'stealth', 'fasttravel', 'phone', 'sms', 'appearance', 'recipes', 'sprint', 'steal', 'terminals' }) do
            if d.features[k] ~= nil then ui[k] = d.features[k] end
        end
    end
    for i, v in ipairs(uiCourage) do if d.courage == v then ui.courage = i end end
    for i, v in ipairs(uiStyle) do if d.style == v then ui.style = i end end
    for i, v in ipairs(uiAggro) do if d.aggro == v then ui.aggro = i end end
    if d.hud ~= nil then hudShow = d.hud and true or false end
    if d.hud_locks ~= nil then hudLocks = d.hud_locks and true or false end
    for _, p in ipairs(hudPosList) do if d.hud_pos == p then hudPos = p end end
    -- reglages deja fixes en jeu : conserves tels quels (sans horodatage pour l ancien format : Python ne s en sert
    -- alors que pour combler un reglage absent du panneau Windows)
    local ts = (d._v == 2 and type(d._changed_at) == 'table') and d._changed_at or {}
    uiCarry, uiStamp = {}, {}
    for _, k in ipairs(UI_KEYS) do
        if d[k] ~= nil and d[k] ~= '' then uiCarry[k] = d[k]; uiStamp[k] = tonumber(ts[k]) end
    end
    if type(d.features) == 'table' then
        for _, k in ipairs(UI_FEATS) do
            if d.features[k] ~= nil then uiCarry['features.' .. k] = d.features[k]; uiStamp['features.' .. k] = tonumber(ts['features.' .. k]) end
        end
    end
    if (uiCarry.provider or 'ollama') == 'ollama' then uiCarry.api_key = nil; uiStamp.api_key = nil end   -- cle inutile avec Ollama
end
local function uiWrite(d)
    local f = io.open('agent_config.json', 'w')
    if not f then ui.saved = 'echec d ecriture'; return false end
    f:write(json.encode(d)); f:close()
    return true
end
local function uiSave()
    local cur = uiValues()
    local now = os.time()
    for k, v in pairs(cur) do
        if v ~= uiSnap[k] then uiCarry[k] = v; uiStamp[k] = now end     -- modifie ici depuis le chargement : horodate
    end
    if (cur.provider or 'ollama') == 'ollama' then uiCarry.api_key = nil; uiStamp.api_key = nil
    elseif uiCarry.provider and uiCarry.api_key == nil and cur.api_key and cur.api_key ~= '' then
        uiCarry.api_key = cur.api_key; uiStamp.api_key = uiStamp.provider    -- cle deja saisie : suit le fournisseur fixe en jeu
    end
    local d = { _v = 2, hud = hudShow, hud_locks = hudLocks, hud_pos = hudPos, _changed_at = {}, features = {} }
    for k, v in pairs(uiCarry) do
        local fk = k:match('^features%.(.+)$')
        if fk then d.features[fk] = v else d[k] = v end
        if uiStamp[k] then d._changed_at[k] = uiStamp[k] end
    end
    if next(d.features) == nil then d.features = nil end
    if next(d._changed_at) == nil then d._changed_at = nil end
    if uiWrite(d) then uiSnap = cur; ui.saved = 'enregistre ' .. os.date('%H:%M:%S') .. ' (effet au prochain lancement de V)' end
end
local uiSyncT0 = nil                         -- session de l agent dont la config effective a deja ete reprise
local function uiSyncEffective(force)
    -- 23/09 : les champs montraient les defauts du panneau (ou l ancien fichier), pas la config reellement utilisee ;
    -- « Enregistrer » sur une valeur affichee ne changeait rien. On aligne sur la config effective publiee par
    -- l agent (table hud, cfgv) les champs que l utilisateur n a PAS touches depuis le dernier chargement.
    local e = hudData and hudData.cfgv
    if type(e) ~= 'table' then return end
    if not force and uiSyncT0 == hudData.t0 then return end
    uiSyncT0 = hudData.t0
    local cur = uiValues()
    local function untouched(k) return cur[k] == uiSnap[k] end
    if untouched('provider') then for i, p in ipairs(uiProviders) do if p == e.provider then ui.provider = i end end end
    for _, k in ipairs({ 'model', 'openai_model', 'anthropic_model' }) do
        if type(e[k]) == 'string' and e[k] ~= '' and untouched(k) then ui[k] = e[k] end
    end
    if tonumber(e.minutes) and untouched('minutes') then ui.minutes = math.floor(tonumber(e.minutes)) end
    for _, p in ipairs({ { 'courage', uiCourage }, { 'style', uiStyle }, { 'aggro', uiAggro } }) do
        if untouched(p[1]) then for i, v in ipairs(p[2]) do if v == e[p[1]] then ui[p[1]] = i end end end
    end
    if type(e.features) == 'table' then
        for _, k in ipairs(UI_FEATS) do
            if type(e.features[k]) == 'boolean' and untouched('features.' .. k) then ui[k] = e.features[k] end
        end
    end
    local after = uiValues()
    for k, v in pairs(after) do if cur[k] == uiSnap[k] then uiSnap[k] = v end end   -- repris de l agent : pas un changement
end
local function uiForget()
    -- « Oublier les reglages in-game » : le panneau Windows redevient seul maitre (cle API effacee du dossier du jeu)
    uiCarry, uiStamp = {}, {}
    if uiWrite({ _v = 2, hud = hudShow, hud_locks = hudLocks, hud_pos = hudPos }) then
        ui.key = ''
        uiSnap = uiValues(); ui.saved = 'reglages in-game oublies ' .. os.date('%H:%M:%S')
        pcall(uiSyncEffective, true)
    end
end
pcall(uiLoad)
uiSnap = uiValues()

-- ============================ HUD « TERMINATOR » ============================
local function hudAgo(t, now)
    t = tonumber(t)
    if not t then return '' end
    local d = math.floor(math.max(0, now - t))
    if d < 60 then return string.format(' (IL Y A %d S)', d) end
    return string.format(' (IL Y A %d MIN)', math.floor(d / 60))
end
local function hudHms(sec)
    sec = math.max(0, math.floor(sec or 0))
    return string.format('%02d:%02d:%02d', math.floor(sec / 3600), math.floor(sec / 60) % 60, sec % 60)
end
local function hudFlags()
    local v = 0
    for _, n in ipairs({ 'NoTitleBar', 'NoResize', 'AlwaysAutoResize', 'NoCollapse', 'NoScrollbar', 'NoFocusOnAppearing' }) do
        local f = ImGuiWindowFlags[n]
        if f then v = (bit and bit.bor) and bit.bor(v, f) or (v + f) end
    end
    return v
end
local function hudBody()
    local e = lastExport or {}
    local d = hudData or {}
    local now = os.time()
    local stale = (tonumber(d.ts) == nil) or (now - tonumber(d.ts) > 8)
    local blink = math.floor(os.clock() * 2) % 2 == 0
    local bd = e.bd or {}
    local mode
    if e.dead then mode = 'UNITE HORS SERVICE'
    elseif bd.active or bd.rew then mode = 'DANSE SENSORIELLE'
    elseif e.combat then mode = 'ENGAGEMENT'
    elseif e.vehicle then mode = 'CONDUITE'
    elseif (e.dialog or {}).choices then mode = 'DIALOGUE'
    elseif stale then mode = 'CONTROLE MANUEL'
    elseif d.paused then mode = 'PAUSE - CONTROLE MANUEL'
    else mode = tostring(d.mode or 'EN MISSION') end
    local link = 'ACTIVE'
    if stale then link = 'PERDUE' elseif d.ended then link = 'SESSION TERMINEE' elseif d.paused then link = 'EN PAUSE' end
    local hp = tonumber(e.hp) or 0

    ImGui.Text('V-800  //  UNITE AUTONOME   ' .. (blink and '#' or ' '))
    ImGui.Separator()
    local fast = (math.floor(os.clock() * 4) % 2 == 0) and 1.0 or 0.35
    if not e.dead and hp < 30 then
        ImGui.TextColored(1.0, 0.9, 0.25, fast, string.format('!! ALERTE : INTEGRITE CRITIQUE (%d %%) !!', math.floor(hp + 0.5)))
    end
    local nFoes = 0
    for _, en in ipairs(e.enemies or {}) do if not en.dead and not en.police then nFoes = nFoes + 1 end end
    if nFoes >= 6 then
        ImGui.TextColored(1.0, 0.9, 0.25, fast, string.format('!! MENACE CRITIQUE : %d HOSTILES OU PLUS !!', nFoes))
    end
    ImGui.Text('MODE ............ ' .. mode)
    ImGui.Text('LIAISON IA ...... ' .. link .. ((d.model and not stale) and ('  [' .. tostring(d.model) .. ']') or ''))
    ImGui.Text(string.format('INTEGRITE ....... %d %%', math.floor(hp + 0.5)))
    ImGui.ProgressBar(math.max(0, math.min(1, hp / 100)), 300, 8, '')
    ImGui.Text('NIVEAU .......... ' .. tostring(e.level or '?'))
    ImGui.Separator()
    ImGui.Text('DIRECTIVE PRIORITAIRE')
    ImGui.Text('> ' .. tostring((not stale and d.directive) or 'AUCUNE (CONTROLE MANUEL)'))
    if not stale and d.decision then ImGui.Text('DECISION ........ ' .. tostring(d.decision)) end
    if d.order then ImGui.Text('ORDRE RECU ...... ' .. tostring(d.order) .. hudAgo(d.order_t, now)) end
    ImGui.Separator()
    local alive, nearest, police = 0, nil, 0
    for _, en in ipairs(e.enemies or {}) do
        if not en.dead then
            alive = alive + 1
            if en.police then police = police + 1 end
            if (not nearest) or (en.d or 999) < nearest then nearest = en.d end
        end
    end
    local lvl = 'NULLE'
    if alive >= 6 then lvl = 'CRITIQUE' elseif alive >= 3 then lvl = 'ELEVEE' elseif alive >= 1 then lvl = 'FAIBLE' end
    if alive > 0 and hp < 35 then lvl = 'CRITIQUE' end
    ImGui.Text(string.format('ANALYSE MENACE .. %s  (%d HOSTILE%s%s)', lvl, alive, alive > 1 and 'S' or '',
        nearest and string.format(', %d M', math.floor(nearest + 0.5)) or ''))
    if police > 0 then ImGui.Text('                  DONT ' .. police .. ' POLICE : NE PAS ENGAGER') end
    ImGui.Text(string.format('PERTES HUMAINES . %d SESSION  /  %d TOTAL', hudKills, hudKillsTotal))
    local sc = hudScan
    if sc and (tonumber(sc.d) or 999) < 80 then
        local dist = string.format('  [%d M]', math.floor((tonumber(sc.d) or 0) + 0.5))
        if sc.cls == 'gamePuppet' then
            ImGui.Separator()
            local nm = (sc.name and sc.name ~= '') and sc.name or sc.aff or 'individu'
            ImGui.Text('ANALYSE CIBLE ... ' .. hudAscii(nm) .. dist)
            local parts = {}
            if sc.aff and sc.aff ~= '' then parts[#parts + 1] = 'FACTION ' .. hudAscii((tostring(sc.aff):gsub('(%l)(%u)', '%1 %2'))) end
            if tonumber(sc.lvl) then parts[#parts + 1] = 'NIVEAU ' .. math.floor(tonumber(sc.lvl) + 0.5) end
            if #parts > 0 then ImGui.Text('                  ' .. table.concat(parts, '  |  ')) end
            local state = sc.dead and 'TERMINE' or (sc.down and 'NEUTRALISE' or (sc.att or '?'))
            if not sc.dead and not sc.down and sc.combat then state = state .. ' (EN COMBAT)' end
            local hpT = (not sc.dead and tonumber(sc.hp)) and string.format('  |  INTEGRITE %d %%', math.floor(tonumber(sc.hp) + 0.5)) or ''
            ImGui.Text('STATUT .......... ' .. state .. hpT)
            local thr = 'NULLE'
            if not sc.dead and not sc.down then
                if sc.police then thr = 'NON-CIBLE (POLICE)'
                elseif sc.att == 'HOSTILE' then
                    local diff = (tonumber(sc.lvl) or 0) - (tonumber(e.level) or 0)
                    thr = (diff >= 5 and 'EXTREME') or (diff >= 0 and 'ELEVEE') or 'MOYENNE'
                elseif sc.combat then thr = 'INSTABLE' end
            end
            ImGui.Text('EVALUATION ...... ' .. thr)
        elseif sc.cls == 'vehicleBaseObject' then
            ImGui.Separator()
            ImGui.Text('ANALYSE CIBLE ... VEHICULE ' .. hudAscii(sc.name or '') .. dist)
        else
            local lbl = ({ gameLootContainerBase = 'CONTENEUR', gameContainerObject = 'CONTENEUR', gameItemDropObject = 'OBJET AU SOL',
                           gameLootBag = 'SAC', gameDevice = 'DISPOSITIF' })[sc.cls]
            if lbl then ImGui.Separator(); ImGui.Text('ANALYSE CIBLE ... ' .. lbl .. dist) end
        end
    end
    ImGui.Separator()
    local st = d.stats or {}
    ImGui.Text(string.format('COMBATS %d   MORTS %d   LOOT %d   EDDIES %+d',
        tonumber(st.combats) or 0, tonumber(st.morts) or 0, tonumber(st.loot) or 0, tonumber(st.eddies) or 0))
    if d.t0 and not stale then ImGui.Text('SESSION ......... ' .. hudHms(now - (tonumber(d.t0) or now))) end
    if d.temper then ImGui.Text('PROTOCOLE ....... ' .. tostring(d.temper)) end
    local evs = {}
    for _, ev in ipairs(d.events or {}) do
        if type(ev) == 'table' and ev[1] and ev[2] then evs[#evs + 1] = { tonumber(ev[1]) or 0, tostring(ev[2]) } end
    end
    for _, ev in ipairs(hudEvents) do evs[#evs + 1] = ev end
    if #evs > 0 then
        table.sort(evs, function(a, b) return a[1] > b[1] end)
        ImGui.Separator()
        ImGui.Text('JOURNAL')
        for i = 1, math.min(5, #evs) do
            ImGui.Text(os.date('%H:%M:%S', math.floor(evs[i][1])) .. '  ' .. evs[i][2])
        end
    end
    if os.clock() - hudHexT > 0.12 then
        hudHexT = os.clock()
        for i = 1, 2 do
            hudHex[i] = string.format('%04X %04X %04X %04X %04X %04X %04X', math.random(0, 65535), math.random(0, 65535),
                math.random(0, 65535), math.random(0, 65535), math.random(0, 65535), math.random(0, 65535), math.random(0, 65535))
        end
    end
    for i = 1, 2 do ImGui.TextColored(0.65, 0.07, 0.05, 0.8, hudHex[i] or '') end
end
local function hudFlagsOf(names)
    local v = 0
    for _, n in ipairs(names) do
        local f = ImGuiWindowFlags[n]
        if f then v = (bit and bit.bor) and bit.bor(v, f) or (v + f) end
    end
    return v
end
local function hudStyled(colors, vars, fn)
    -- pousse/retire les styles de facon equilibree meme si un appel echoue (sinon ils fuiraient sur les autres fenetres)
    local nc, nv = 0, 0
    pcall(function()
        for _, c in ipairs(colors) do if c[1] ~= nil then ImGui.PushStyleColor(c[1], c[2], c[3], c[4], c[5]); nc = nc + 1 end end
        for _, v in ipairs(vars) do
            if v[1] ~= nil then
                if v[3] then ImGui.PushStyleVar(v[1], v[2], v[3]) else ImGui.PushStyleVar(v[1], v[2]) end
                nv = nv + 1
            end
        end
    end)
    local ok, err = pcall(fn)
    if nv > 0 then ImGui.PopStyleVar(nv) end
    if nc > 0 then ImGui.PopStyleColor(nc) end
    if not ok and tostring(err) ~= hudLastErr then hudLastErr = tostring(err); journal('HUD cadres : ' .. hudLastErr) end
end
local function drawLocks()
    local targets = hudLockTargets
    if not (hudLocks and targets and #targets > 0) then return end
    local okR, W, H = pcall(GetDisplayResolution)
    if not okR or not W or not H or W <= 0 or H <= 0 then return end
    local boxFlags = hudFlagsOf({ 'NoTitleBar', 'NoResize', 'NoMove', 'NoScrollbar', 'NoCollapse', 'NoSavedSettings', 'NoFocusOnAppearing', 'NoInputs' })
    local tagFlags = hudFlagsOf({ 'NoTitleBar', 'NoResize', 'NoMove', 'NoScrollbar', 'NoCollapse', 'NoSavedSettings', 'NoFocusOnAppearing', 'NoInputs', 'AlwaysAutoResize' })
    local locked = false
    for i, t in ipairs(targets) do
        if i > 6 then break end
        local h = math.max(40, math.min(0.6 * H, 1.4 * H / math.max(t.d, 1.5)))
        local w = h * 0.5
        local px, py = (t.sx + 1) * 0.5 * W, (1 - t.sy) * 0.5 * H
        local r, g, b, label, thick = 1.0, 0.12, 0.08, 'HOSTILE', 1.5
        if t.police then r, g, b, label = 1.0, 0.7, 0.1, 'POLICE : NON-CIBLE'
        elseif not locked then label, thick, locked = 'CIBLE VERROUILLEE', 2.5, true end
        hudStyled({ { ImGuiCol.WindowBg, 0, 0, 0, 0.0 }, { ImGuiCol.Border, r, g, b, 0.95 } },
                  { { ImGuiStyleVar.WindowBorderSize, thick }, { ImGuiStyleVar.WindowRounding, 0.0 }, { ImGuiStyleVar.WindowMinSize, 1.0, 1.0 } },
                  function()
                      ImGui.SetNextWindowPos(px - w / 2, py - h / 2, ImGuiCond.Always)
                      ImGui.SetNextWindowSize(w, h, ImGuiCond.Always)
                      ImGui.Begin('##agentlockbox' .. i, boxFlags)
                      ImGui.End()
                  end)
        hudStyled({ { ImGuiCol.WindowBg, 0, 0, 0, 0.35 }, { ImGuiCol.Text, r, g, b, 1.0 } },
                  { { ImGuiStyleVar.WindowBorderSize, 0.0 }, { ImGuiStyleVar.WindowPadding, 3.0, 1.0 } },
                  function()
                      ImGui.SetNextWindowPos(px - w / 2, py - h / 2 - 34, ImGuiCond.Always)
                      if ImGui.Begin('##agentlocktag' .. i, tagFlags) then
                          pcall(function()
                              ImGui.SetWindowFontScale(0.85)
                              ImGui.Text(label)
                              ImGui.Text(string.format('%d M', math.floor(t.d + 0.5)))
                          end)
                      end
                      ImGui.End()
                  end)
    end
end
local function hudRead()
    if os.clock() - hudReadT <= 0.5 then return end
    hudReadT = os.clock()
    pcall(function()
        for row in db:nrows('SELECT json FROM hud WHERE id = 1') do
            local okJ, dj = pcall(json.decode, row.json)
            if okJ and type(dj) == 'table' then
                if dj.t0 and dj.t0 ~= hudSessionT0 then hudSessionT0 = dj.t0; hudKills = 0 end   -- nouvelle session de l agent
                hudData = dj
            end
        end
    end)
end
-- 19/10 HUD V-800 « ANATOMIE DU SIGNAL » (Olivier : « redesign le hud du v800 » puis « applique-le en jeu ») : tout est
-- dessine en primitives (lignes, cases, chiffres 7 segments nets a toute taille) sur la liste de dessin d arriere-plan
-- d ImGui, sans fenetre ni entree captee. Un seul rouge de signal en 3 intensites, blanc os pour les valeurs.
-- Globales seulement (limite d upvalues du fichier). Erreur -> retour automatique a l ancien HUD (drawHud).
AgentHudStyle = AgentHudStyle or 'v800'
AgentHudColor = AgentHudColor or 'bleu'          -- 19/10 : Olivier prefere le bleu
AgentV8 = AgentV8 or { textSized = nil, ramMax = nil, money = nil, slowT = -99 }
function AgentV8Col(r, g, b, a)
    local function c(v) return math.max(0, math.min(255, math.floor(v * 255 + 0.5))) end
    local v = c(a) * 16777216 + c(b) * 65536 + c(g) * 256 + c(r)
    if v >= 2147483648 then v = v - 4294967296 end   -- entier signe 32 bits (meme motif binaire pour ImU32)
    return v
end
-- 19/10 : CET d Olivier n a pas GetBackgroundDrawList -> repli : fenetre plein ecran transparente (sans entree ni fond)
-- et sa liste de dessin. Les fonctions de dessin presentes sont journalisees une fois (diagnostic).
function AgentV8Probe()
    if AgentV8.probed then return end
    AgentV8.probed = true
    local names = {}
    pcall(function() for k, _ in pairs(ImGui) do if tostring(k):find('Draw') then names[#names + 1] = tostring(k) end end end)
    table.sort(names)
    pcall(journal, 'HUD V-800 : fonctions de dessin ImGui disponibles : ' .. (#names > 0 and table.concat(names, ', ') or 'AUCUNE'))
end
function AgentDrawV800(e, d, kills)
    if type(e) ~= 'table' then return end
    d = type(d) == 'table' and d or {}
    local okR, W, H = pcall(GetDisplayResolution)
    if not (okR and W and H and W > 0 and H > 0) then return end
    AgentV8Probe()
    for _, getter in ipairs({ 'GetBackgroundDrawList', 'GetForegroundDrawList' }) do
        if ImGui[getter] then
            local okB, bg = pcall(ImGui[getter])
            if okB and bg then return AgentV8Body(bg, W, H, e, d, kills) end
        end
    end
    if not ImGui.GetWindowDrawList then error('aucune liste de dessin ImGui (ni arriere-plan, ni fenetre)') end
    local flags = 0
    for _, n in ipairs({ 'NoTitleBar', 'NoResize', 'NoMove', 'NoScrollbar', 'NoScrollWithMouse', 'NoCollapse', 'NoSavedSettings',
                         'NoInputs', 'NoBackground', 'NoFocusOnAppearing', 'NoBringToFrontOnFocus', 'NoNav' }) do
        local f = ImGuiWindowFlags[n]
        if f then flags = (bit and bit.bor) and bit.bor(flags, f) or (flags + f) end
    end
    ImGui.SetNextWindowPos(0, 0, ImGuiCond.Always)
    ImGui.SetNextWindowSize(W, H, ImGuiCond.Always)
    ImGui.PushStyleColor(ImGuiCol.WindowBg, 0, 0, 0, 0)
    ImGui.PushStyleColor(ImGuiCol.Border, 0, 0, 0, 0)
    local nv = 0
    if ImGuiStyleVar.WindowBorderSize then ImGui.PushStyleVar(ImGuiStyleVar.WindowBorderSize, 0.0); nv = nv + 1 end
    if ImGuiStyleVar.WindowPadding then ImGui.PushStyleVar(ImGuiStyleVar.WindowPadding, 0.0, 0.0); nv = nv + 1 end
    local ok, err = true, nil
    if ImGui.Begin('##agentv800', flags) then
        ok, err = pcall(function() AgentV8Body(ImGui.GetWindowDrawList(), W, H, e, d, kills) end)
    end
    ImGui.End()
    if nv > 0 then ImGui.PopStyleVar(nv) end
    ImGui.PopStyleColor(2)
    if not ok then error(err) end
end
function AgentV8Body(dl, W, H, e, d, kills)
    local u = H / 1080
    local M = 56 * u
    local now, clk = os.time(), os.clock()
    local blink = math.floor(clk * 2) % 2 == 0
    -- couleurs
    -- 19/10 : intensites relevees pour une vraie scene de jeu (claire) ; plaques sombres derriere les colonnes
    -- 19/10 (Olivier : « je prefere le bleu au rouge ») : palette BLEUE par defaut (touche CET pour revenir au rouge) ;
    -- les noms RED* restent ceux des 3 intensites du signal. Le flash de degat reste rouge, la police ambre.
    local RED, RED_MID, RED_DIM, RED_FAINT, BONE, SHADE, PLATE
    if AgentHudColor == 'rouge' then
        RED, RED_MID = AgentV8Col(1, 0.17, 0.10, 1), AgentV8Col(0.86, 0.16, 0.10, 1)
        RED_DIM, RED_FAINT = AgentV8Col(0.62, 0.13, 0.09, 1), AgentV8Col(0.34, 0.08, 0.06, 0.9)
        BONE = AgentV8Col(0.91, 0.86, 0.82, 0.92)
        SHADE, PLATE = AgentV8Col(0.03, 0.0, 0.0, 0.55), AgentV8Col(0.04, 0.01, 0.01, 0.42)
    else
        RED, RED_MID = AgentV8Col(0.24, 0.76, 1.0, 1), AgentV8Col(0.16, 0.56, 0.88, 1)
        RED_DIM, RED_FAINT = AgentV8Col(0.12, 0.40, 0.66, 1), AgentV8Col(0.06, 0.18, 0.32, 0.9)
        BONE = AgentV8Col(0.86, 0.94, 1.0, 0.94)
        SHADE, PLATE = AgentV8Col(0.0, 0.01, 0.03, 0.55), AgentV8Col(0.01, 0.02, 0.05, 0.42)
    end
    local AMBER = AgentV8Col(1.0, 0.72, 0.15, 1)
    local SHADOW = AgentV8Col(0, 0, 0, 0.8)
    local function L(x1, y1, x2, y2, col, th) ImGui.ImDrawListAddLine(dl, x1, y1, x2, y2, col, (th or 1) * u) end
    local function R(x1, y1, x2, y2, col) ImGui.ImDrawListAddRectFilled(dl, x1, y1, x2, y2, col) end
    local function RO(x1, y1, x2, y2, col, th) ImGui.ImDrawListAddRect(dl, x1, y1, x2, y2, col, 0, 0, (th or 1) * u) end
    local fs0 = 13
    pcall(function() fs0 = ImGui.GetFontSize() end)
    if AgentV8.textSized == nil then
        AgentV8.textSized = pcall(function() ImGui.ImDrawListAddText(dl, 12, -500, -500, RED, '.') end)
    end
    local function TW(s, size)
        local w = 0
        pcall(function() w = ImGui.CalcTextSize(s) end)
        return w * ((AgentV8.textSized and size or fs0) / fs0)
    end
    local function T(x, y, s, size, col, align)
        s = tostring(s or '')
        size = (size or 13) * u
        if align == 'r' then x = x - TW(s, size) elseif align == 'c' then x = x - TW(s, size) / 2 end
        local o = math.max(1, u)
        if AgentV8.textSized then
            ImGui.ImDrawListAddText(dl, size, x + o, y + o, SHADOW, s)
            ImGui.ImDrawListAddText(dl, size, x, y, col, s)
        else
            ImGui.ImDrawListAddText(dl, x + o, y + o, SHADOW, s)
            ImGui.ImDrawListAddText(dl, x, y, col, s)
        end
    end
    local function arc(cx, cy, r, a0, a1, col, th, n)
        n = n or 12
        local px, py
        for i = 0, n do
            local a = math.rad(a0 + (a1 - a0) * i / n)
            local x, y = cx + r * math.cos(a), cy + r * math.sin(a)
            if px then L(px, py, x, y, col, th) end
            px, py = x, y
        end
    end
    -- chiffres 7 segments (nets a toute taille : pas de police agrandie floue)
    local SEG = { ['0'] = 'abcdef', ['1'] = 'bc', ['2'] = 'abged', ['3'] = 'abgcd', ['4'] = 'fgbc', ['5'] = 'afgcd',
                  ['6'] = 'afgedc', ['7'] = 'abc', ['8'] = 'abcdefg', ['9'] = 'abcfgd', ['-'] = 'g' }
    local function seg7(x, y, h, str, col, th)
        local w, gap = h * 0.46, h * 0.2
        for ch in tostring(str):gmatch('.') do
            local sg = SEG[ch] or ''
            local x0, x1, ym, y1 = x, x + w, y + h / 2, y + h
            local P = { a = { x0, y, x1, y }, b = { x1, y, x1, ym }, c = { x1, ym, x1, y1 }, d = { x0, y1, x1, y1 },
                        e = { x0, ym, x0, y1 }, f = { x0, y, x0, ym }, g = { x0, ym, x1, ym } }
            for s in sg:gmatch('.') do local p = P[s]; L(p[1], p[2], p[3], p[4], col, th) end
            x = x + w + gap
        end
        return x
    end

    -- donnees lentes (1 fois / 2 s) : RAM max, eddies
    if clk - AgentV8.slowT > 2.0 then
        AgentV8.slowT = clk
        pcall(function()
            local p = Game.GetPlayer()
            if p then
                pcall(function() AgentV8.ramMax = math.floor(Game.GetStatsSystem():GetStatValue(p:GetEntityID(), gamedataStatType.Memory) + 0.5) end)
                pcall(function() AgentV8.money = Game.GetTransactionSystem():GetItemQuantity(p, MarketSystem.Money()) end)
            end
        end)
    end

    local hp = tonumber(e.hp) or 0
    local stale = (tonumber(d.ts) == nil) or (now - tonumber(d.ts) > 8)
    local foes, police = {}, 0
    for _, en in ipairs(e.enemies or {}) do
        if not en.dead then
            if en.police then police = police + 1 else foes[#foes + 1] = en end
        end
    end
    table.sort(foes, function(a, b) return (a.d or 999) < (b.d or 999) end)
    local alert = (not e.dead) and (hp < 30 or #foes >= 6)
    -- 19/10 (Olivier : « ameliore le HUD ») : etat d animation (demarrage, degats, eliminations, verrou)
    if AgentV8.sess ~= d.t0 then AgentV8.sess = d.t0; AgentV8.bootT = clk end
    local boot = math.min(1, (clk - (AgentV8.bootT or -99)) / 2.5)
    if AgentV8.lastHp and hp < AgentV8.lastHp - 4 and not e.dead then AgentV8.hitT = clk end
    AgentV8.lastHp = hp
    if AgentV8.kills and (tonumber(kills) or 0) > AgentV8.kills then AgentV8.killT = clk end
    AgentV8.kills = tonumber(kills) or 0
    local function wrapd(a) return ((a + 180) % 360) - 180 end
    local function bearing(x1, y1) return math.deg(math.atan2(-(x1 - e.x), y1 - e.y)) end
    local CX, CY = W / 2, H / 2
    local hitAge = clk - (AgentV8.hitT or -99)
    if hitAge < 0.45 then                              -- flash rouge sur les bords quand V encaisse
        local k = 1 - hitAge / 0.45
        for i = 0, 3 do
            local th = (6 + i * 12) * u
            local c = AgentV8Col(1, 0.1, 0.05, 0.24 * k * (1 - i / 4))
            R(0, 0, W, th, c); R(0, H - th, W, H, c); R(0, 0, th, H, c); R(W - th, 0, W, H, c)
        end
    end
    if boot < 1 then                                   -- sequence de demarrage (2,5 s a chaque session)
        T(CX, CY - 44 * u, 'V-800  //  INITIALISATION', 22, RED, 'c')
        for i = 0, 59 do
            local x = CX - 240 * u + i * 8 * u
            L(x, CY, x, CY + 14 * u, (i / 60) < boot and RED or RED_FAINT, 2)
        end
        local stages = { 'NOYAU', 'VITAUX', 'REGISTRE', 'OPTIQUE', 'NAVIGATION' }
        T(CX, CY + 30 * u, 'CHARGEMENT : ' .. stages[math.min(5, math.floor(boot * 5) + 1)], 11, RED_MID, 'c')
    end

    -- ===== centre : reticule (complet en combat, anneau seul sinon) =====
    if boot >= 0.55 then
    local inCombat = e.combat or #foes > 0
    local ringCol = inCombat and RED or RED_DIM
    for deg = 0, 355, 5 do
        local a = math.rad(deg)
        local long_ = deg % 30 == 0
        if inCombat or long_ then
            local r0, r1 = (long_ and 150 or 156) * u, (long_ and 166 or 162) * u
            L(CX + r0 * math.cos(a), CY + r0 * math.sin(a), CX + r1 * math.cos(a), CY + r1 * math.sin(a),
              long_ and ringCol or RED_DIM, long_ and 1.3 or 1)
        end
    end
    if inCombat then
        for a0 = 0, 354, 6 do arc(CX, CY, 118 * u, a0, a0 + 3, RED_MID, 1, 2) end
    end

    -- anneau des menaces : chaque hostile (meme hors champ, derriere V) = un chevron a son cap autour du reticule
    if tonumber(e.x) and tonumber(e.y) and tonumber(e.yaw) then
        for _, t in ipairs(foes) do
            if tonumber(t.x) and tonumber(t.y) then
                local rel = wrapd(bearing(t.x, t.y) - e.yaw)
                local a, hw = math.rad(-90 - rel), math.rad(2.4)
                local r0, r1 = 178 * u, 194 * u
                local col = (t.d or 99) < 20 and RED or RED_MID
                ImGui.ImDrawListAddTriangleFilled(dl, CX + r0 * math.cos(a), CY + r0 * math.sin(a),
                    CX + r1 * math.cos(a - hw), CY + r1 * math.sin(a - hw), CX + r1 * math.cos(a + hw), CY + r1 * math.sin(a + hw), col)
            end
        end
    end
    -- ===== cibles : verrou sur la plus proche, crochets fins sur les autres =====
    local locked = nil
    for i, t in ipairs(foes) do
        if i > 6 then break end
        local sx, sy = tonumber(t.sx), tonumber(t.sy)
        if sx and sy and math.abs(sx) <= 1.05 and math.abs(sy) <= 1.05 then
            local h = math.max(40 * u, math.min(0.6 * H, 1.4 * H / math.max(t.d or 10, 1.5)))
            local w = h * 0.5
            local px, py = (sx + 1) * 0.5 * W, (1 - sy) * 0.5 * H
            local x0, y0, x1, y1 = px - w / 2, py - h / 2, px + w / 2, py + h / 2
            local cl = math.min(22 * u, w * 0.3)
            local first = locked == nil
            if first then                              -- verrouillage anime : les crochets se referment sur la cible
                local key = tostring(t.id or t.name or '?')
                if AgentV8.lockKey ~= key then AgentV8.lockKey, AgentV8.lockT = key, clk end
                local g = 1 + 0.7 * math.max(0, 1 - (clk - AgentV8.lockT) / 0.35)
                local mx_, my_ = (x0 + x1) / 2, (y0 + y1) / 2
                x0, x1, y0, y1 = mx_ - (mx_ - x0) * g, mx_ + (x1 - mx_) * g, my_ - (my_ - y0) * g, my_ + (y1 - my_) * g
            end
            local col, th = first and RED or RED_MID, first and 2 or 1.2
            for _, k in ipairs({ { x0, y0, 1, 1 }, { x1, y0, -1, 1 }, { x0, y1, 1, -1 }, { x1, y1, -1, -1 } }) do
                L(k[1], k[2], k[1] + k[3] * cl, k[2], col, th); L(k[1], k[2], k[1], k[2] + k[4] * cl, col, th)
            end
            if first then
                locked = t
                local yy = y0 + 14 * u
                while yy < y1 - 12 * u do L(x0 + 10 * u, yy, x1 - 10 * u, yy, RED_FAINT, 1); yy = yy + 9.4 * u end
                local my = (y0 + y1) / 2
                local ang = math.atan2(my - CY, x0 - CX)
                if px > CX + 170 * u or px < CX - 170 * u then
                    L(CX + 166 * u * math.cos(ang), CY + 166 * u * math.sin(ang), x0 - 12 * u, my, RED_MID, 1)
                end
                T(x0, y0 - 46 * u, 'CIBLE 01', 18, RED)
                local acq = clk - (AgentV8.lockT or -99) < 0.6
                T(x0, y0 - 22 * u, acq and 'ACQUISITION...' or 'VERROUILLEE', 11, acq and BONE or RED_DIM)
                local rows = { { 'DIST', string.format('%d M', math.floor((t.d or 0) + 0.5)) } }
                if t.name and t.name ~= '' then rows[#rows + 1] = { 'CLASSE', string.upper(tostring(t.name)):sub(1, 22) } end
                for k, rw in ipairs(rows) do
                    T(x0, y1 + 10 * u + (k - 1) * 20 * u, rw[1], 11, RED_DIM)
                    T(x0 + 64 * u, y1 + 10 * u + (k - 1) * 20 * u, rw[2], 11, BONE)
                end
                local ty = y1 + 10 * u + #rows * 20 * u
                T(x0, ty, 'MENACE', 11, RED_DIM)
                local lvl = (#foes >= 6 or hp < 35) and 5 or (#foes >= 3 and 3 or 1)
                for k = 0, 4 do
                    local tx = x0 + 64 * u + k * 13 * u
                    if k < lvl then ImGui.ImDrawListAddTriangleFilled(dl, tx, ty + 11 * u, tx + 4.5 * u, ty + 2 * u, tx + 9 * u, ty + 11 * u, RED)
                    else L(tx, ty + 11 * u, tx + 4.5 * u, ty + 2 * u, RED_DIM, 1); L(tx + 4.5 * u, ty + 2 * u, tx + 9 * u, ty + 11 * u, RED_DIM, 1); L(tx, ty + 11 * u, tx + 9 * u, ty + 11 * u, RED_DIM, 1) end
                end
            else
                T(x0, y1 + 6 * u, string.format('%d M', math.floor((t.d or 0) + 0.5)), 10, RED_DIM)
            end
        end
    end
    for _, en in ipairs(e.enemies or {}) do         -- police : crochets ambre, jamais une cible
        if en.police and not en.dead and tonumber(en.sx) and math.abs(en.sx) <= 1 and math.abs(en.sy or 9) <= 1 then
            local h = math.max(40 * u, math.min(0.5 * H, 1.4 * H / math.max(en.d or 10, 1.5)))
            local px, py = (en.sx + 1) * 0.5 * W, (1 - en.sy) * 0.5 * H
            RO(px - h / 4, py - h / 2, px + h / 4, py + h / 2, AMBER, 1)
            T(px - h / 4, py - h / 2 - 18 * u, 'POLICE : NON-CIBLE', 10, AMBER)
        end
    end
    if not locked then AgentV8.lockKey = nil end
    end   -- boot >= 0.55

    -- ===== haut centre : narrateur (ou directive en veille) =====
    local NY = 92 * u
    local sayAge = (d.say and tonumber(d.say_t)) and (now - tonumber(d.say_t)) or 99
    if d.say and sayAge >= 0 and sayAge <= 7 then
        AgentSaySeen = AgentSaySeen or {}
        if AgentSaySeen.t ~= d.say_t then AgentSaySeen.t, AgentSaySeen.c0 = d.say_t, clk end
        local txt = '> ' .. tostring(d.say)
        local n = math.min(#txt, math.floor((clk - AgentSaySeen.c0) * 45) + 1)
        local size = 34
        local wFull = TW(txt, size * u)
        local bx0, bx1 = CX - wFull / 2 - 40 * u, CX + wFull / 2 + 64 * u
        R(bx0, NY, bx1, NY + 86 * u, SHADE)
        L(bx0, NY, bx0 + 18 * u, NY, RED, 1.4); L(bx0, NY, bx0, NY + 18 * u, RED, 1.4)
        L(bx1 - 18 * u, NY + 86 * u, bx1, NY + 86 * u, RED, 1.4); L(bx1, NY + 68 * u, bx1, NY + 86 * u, RED, 1.4)
        L(bx0 + 30 * u, NY, bx1, NY, RED_DIM, 1); L(bx0, NY + 86 * u, bx1 - 30 * u, NY + 86 * u, RED_DIM, 1)
        local shown = txt:sub(1, n)
        T(CX - wFull / 2 - 12 * u, NY + 43 * u - size * u / 2, shown, size, RED)
        if n < #txt or blink then
            local cx = CX - wFull / 2 - 12 * u + TW(shown, size * u) + 4 * u
            R(cx, NY + 26 * u, cx + 14 * u, NY + 60 * u, RED)
        end
        T(bx0, NY - 20 * u, 'NARRATEUR', 10, RED_DIM)
        T(bx1, NY - 20 * u, 'V-800 / VOIX 01', 10, RED_DIM, 'r')
        local typing = n < #txt
        local nb = 96
        for i = 0, nb - 1 do                               -- onde sonore : vivante pendant l ecriture
            local x = bx0 + i * (bx1 - bx0) / (nb - 1)
            local env = math.sin(math.pi * i / (nb - 1)) ^ 0.7
            local amp = typing and (6 + 16 * math.abs(math.sin(i * 0.61 + clk * 9) * math.cos(i * 0.23 - clk * 5))) or 2
            local hh = 2 * u + env * amp * u
            L(x, NY + 110 * u - hh / 2, x, NY + 110 * u + hh / 2, env > 0.35 and RED_MID or RED_DIM, 1.4)
        end
    elseif not stale and d.directive then
        local s = '> ' .. tostring(d.directive)
        T(CX, NY + 30 * u, s, 16, RED_MID, 'c')
        T(CX, NY + 8 * u, 'DIRECTIVE PRIORITAIRE', 10, RED_DIM, 'c')
    end

    -- ===== gauche : colonne vitale =====
    if boot >= 0.15 then
    local LX = M + 8 * u
    R(M - 10 * u, 80 * u, M + 262 * u, 966 * u, PLATE)
    T(LX, 92 * u, (stale and 'LIAISON PERDUE') or (d.paused and 'EN PAUSE') or 'SYS.ONLINE', 10, stale and AMBER or RED_DIM)
    T(LX, 108 * u, os.date('%H:%M:%S'), 10, RED_MID)
    T(LX, 168 * u, 'VIE', 13, RED_DIM)
    local hpCol = (alert and hp < 30 and not blink) and BONE or RED
    local xEnd = seg7(LX, 192 * u, 80 * u, string.format('%d', math.floor(hp + 0.5)), hpCol, 3)
    T(xEnd + 2 * u, 252 * u, '%', 20, RED_MID)
    local lit = math.floor(hp / 2 + 0.5)
    for i = 0, 49 do
        local y = 330 * u + i * 7.2 * u
        local on = i >= 50 - lit
        local major = i % 5 == 0
        local col = on and (major and RED or RED_MID) or RED_FAINT
        if on and alert and hp < 30 and blink then col = BONE end
        L(LX, y, LX + (major and 40 or 26) * u, y, col, major and 2 or 1.2)
    end
    T(LX + 56 * u, 326 * u, '100', 9, RED_DIM)
    T(LX + 56 * u, 330 * u + 49 * 7.2 * u - 8 * u, '0', 9, RED_DIM)
    local ry = 730 * u
    local ram, ramMax = tonumber(e.ram), tonumber(AgentV8.ramMax)
    T(LX, ry, 'RAM', 13, RED_DIM)
    if ram then
        local nCells = math.max(1, math.min(16, ramMax or math.max(8, math.ceil(ram))))
        local step = math.min(26, 210 / nCells) * u
        for i = 0, nCells - 1 do
            local x = LX + i * step
            if i < math.floor(ram + 0.001) then R(x, ry + 24 * u, x + step * 0.7, ry + 42 * u, RED)
            else RO(x, ry + 24 * u, x + step * 0.7, ry + 42 * u, RED_DIM, 1) end
        end
    end
    local function kv(y, k, v, col)
        T(LX, y, k, 13, RED_DIM); T(LX + 120 * u, y, v, 13, col or BONE)
    end
    kv(ry + 64 * u, 'NIVEAU', tostring(e.level or '?'))
    kv(ry + 88 * u, 'EDDIES', AgentV8.money and tostring(math.floor(AgentV8.money)) or '?')
    L(LX, ry + 120 * u, LX + 210 * u, ry + 120 * u, RED_DIM, 1)
    local bd = e.bd or {}
    local mode
    if e.dead then mode = 'HORS SERVICE'
    elseif bd.active or bd.rew then mode = 'DANSE SENSORIELLE'
    elseif e.combat then mode = 'ENGAGEMENT'
    elseif e.vehicle then mode = 'CONDUITE'
    elseif (e.dialog or {}).choices then mode = 'DIALOGUE'
    elseif stale then mode = 'MANUEL'
    elseif d.paused then mode = 'PAUSE'
    else mode = tostring(d.mode or 'EN MISSION') end
    kv(ry + 132 * u, 'MODE', mode:sub(1, 22), RED)
    kv(ry + 156 * u, 'HOSTILES', tostring(#foes) .. (police > 0 and (' +' .. police .. ' POLICE') or ''), #foes > 0 and RED or BONE)
    kv(ry + 180 * u, 'ELIMINES', tostring(kills or 0))
    if clk - (AgentV8.killT or -99) < 1.5 then T(LX + 170 * u, ry + 180 * u, '+1', 13, BONE) end
    if alert then
        T(LX, ry + 212 * u, hp < 30 and 'INTEGRITE CRITIQUE' or 'MENACE CRITIQUE', 13, blink and BONE or RED)
    end

    end   -- boot >= 0.15
    -- ===== droite : registre des succes =====
    if boot >= 0.35 then
    local gv = type(d.goalsv) == 'table' and d.goalsv or nil
    local RX1 = W - M - 8 * u
    local RX0 = RX1 - 418 * u
    if gv then
        local hy = 216 * u                                -- hauteur du registre (plaque a sa taille)
        local cw = (RX1 - RX0) / 32
        for _, it in ipairs(gv.items or {}) do
            if hy > 0.62 * H then break end
            local cur, tot = tonumber(it.cur), tonumber(it.tot)
            hy = hy + 26 * u
            if cur and tot and tot > 20 then hy = hy + math.ceil(tot / 32) * cw + 18 * u
            elseif cur and tot and tot > 0 then hy = hy + 26 * u
            else hy = hy + ((it.note and cur) and 18 * u or 0) + 8 * u end
        end
        if type(gv.top) == 'table' and #gv.top > 0 then hy = hy + 40 * u + math.min(3, #gv.top) * 24 * u end
        R(RX0 - 16 * u, 80 * u, RX1 + 16 * u, hy + 10 * u, PLATE)
        T(RX0, 92 * u, 'REGISTRE', 10, RED_DIM)
        T(RX1, 92 * u, 'STEAM', 10, RED_DIM, 'r')
        local st = gv.steam or {}
        local xs = seg7(RX0, 118 * u, 56 * u, string.format('%02d', tonumber(st[1]) or 0), RED, 2.6)
        T(xs + 4 * u, 160 * u, '/ ' .. tostring(st[2] or '?') .. ' SUCCES', 18, RED_MID)
        L(RX0, 200 * u, RX1, 200 * u, RED_DIM, 1)
        local y = 216 * u
        for _, it in ipairs(gv.items or {}) do
            if y > 0.62 * H then break end
            local cur, tot = tonumber(it.cur), tonumber(it.tot)
            T(RX0, y, tostring(it.label or ''), 13, BONE)
            local val = (cur and tot) and string.format('%d / %d', cur, tot) or (cur and tostring(cur)) or tostring(it.note or '')
            T(RX1, y, val, 12, RED, 'r')
            y = y + 26 * u
            if cur and tot and tot > 20 then           -- grille dense : une cellule par lieu / element
                local cols = 32
                local cell = (RX1 - RX0) / cols * 0.78
                local gap = (RX1 - RX0) / cols - cell
                for i = 0, tot - 1 do
                    local r_, c_ = math.floor(i / cols), i % cols
                    local x, yy = RX0 + c_ * (cell + gap), y + r_ * (cell + gap)
                    if i < cur then R(x, yy, x + cell, yy + cell, i == cur - 1 and BONE or RED)
                    else RO(x, yy, x + cell, yy + cell, RED_DIM, 1) end
                end
                y = y + math.ceil(tot / cols) * (cell + gap) + 18 * u
            elseif cur and tot and tot > 0 then
                local step = (RX1 - RX0) / tot
                for i = 0, tot - 1 do R(RX0 + i * step, y, RX0 + i * step + step * 0.8, y + 8 * u, i < cur and RED or RED_FAINT) end
                y = y + 26 * u
            else
                if it.note and cur then T(RX0, y - 4 * u, tostring(it.note), 11, RED_MID); y = y + 18 * u end
                y = y + 8 * u
            end
        end
        if type(gv.top) == 'table' and #gv.top > 0 then
            L(RX0, y, RX1, y, RED_DIM, 1)
            T(RX0, y + 14 * u, 'TOP CHAT', 13, BONE)
            local best = tonumber((gv.top[1] or {})[2]) or 1
            for i, row in ipairs(gv.top) do
                if i > 3 then break end
                local yy = y + 40 * u + (i - 1) * 24 * u
                T(RX0, yy, string.format('%02d', i), 11, RED_DIM)
                T(RX0 + 34 * u, yy, tostring(row[1] or ''):sub(1, 12), 12, i == 1 and RED or RED_MID)
                local pts = tonumber(row[2]) or 0
                L(RX0 + 150 * u, yy + 8 * u, RX0 + 150 * u + 200 * u * pts / math.max(best, 1), yy + 8 * u, i == 1 and RED or RED_DIM, 3)
                T(RX1, yy, tostring(pts), 11, RED_MID, 'r')
            end
        end
    elseif type(d.goals) == 'table' and #d.goals > 0 then   -- agent plus ancien : lignes deja formatees
        for i, line in ipairs(d.goals) do T(RX1, 92 * u + (i - 1) * 22 * u, tostring(line), 13, i == 1 and RED or RED_MID, 'r') end
    end

    end   -- boot >= 0.35
    -- ===== bas centre : cap (+ objectif) =====
    if boot >= 0.75 then
    local yaw = tonumber(e.yaw)
    if yaw then
        local heading = (-yaw) % 360
        local CYc = 922 * u
        local half = 400 * u
        R(CX - half - 14 * u, CYc - 40 * u, CX + half + 14 * u, CYc + 58 * u, PLATE)
        L(CX - half, CYc, CX + half, CYc, RED_DIM, 1)
        local base = math.floor(heading)
        for deg = base - 60, base + 61 do
            local x = CX + (deg - heading) * (2 * half / 120)
            if x >= CX - half and x <= CX + half then
                local dm = deg % 360
                if dm % 15 == 0 then
                    L(x, CYc - 12 * u, x, CYc, RED_MID, 1.2)
                    local lab = ({ [0] = 'N', [90] = 'E', [180] = 'S', [270] = 'O' })[dm] or string.format('%03d', dm)
                    T(x, CYc - 30 * u, lab, 10, (#lab == 1) and RED or RED_DIM, 'c')
                elseif dm % 5 == 0 then
                    L(x, CYc - 6 * u, x, CYc, RED_DIM, 1)
                end
            end
        end
        ImGui.ImDrawListAddTriangleFilled(dl, CX, CYc + 3 * u, CX - 6 * u, CYc + 13 * u, CX + 6 * u, CYc + 13 * u, RED)
        T(CX, CYc + 20 * u, string.format('%03d', math.floor(heading + 0.5) % 360), 12, BONE, 'c')
        -- objectif sur le cap : losange a son cap (fleche au bord s il est hors de la bande) + nom et distance
        local tg = type(d.target) == 'table' and d.target or nil
        local tx, ty, tl
        if tg and tonumber(tg.x) and tonumber(tg.y) then tx, ty, tl = tonumber(tg.x), tonumber(tg.y), tg.label
        elseif type(e.quest) == 'table' and tonumber(e.quest.mx) and tonumber(e.quest.my) then tx, ty, tl = tonumber(e.quest.mx), tonumber(e.quest.my), 'QUETE SUIVIE' end
        if tx and tonumber(e.x) and tonumber(e.y) then
            local dd = wrapd(((-bearing(tx, ty)) % 360) - heading)
            local dist = math.sqrt((tx - e.x) ^ 2 + (ty - e.y) ^ 2)
            local s = 2 * half / 120
            if math.abs(dd) <= 60 then
                local xm, ym = CX + dd * s, CYc - 6 * u
                ImGui.ImDrawListAddTriangleFilled(dl, xm, ym - 7 * u, xm - 6 * u, ym, xm + 6 * u, ym, BONE)
                ImGui.ImDrawListAddTriangleFilled(dl, xm - 6 * u, ym, xm + 6 * u, ym, xm, ym + 7 * u, BONE)
            else
                local sg = dd > 0 and 1 or -1
                local xe = CX + sg * (half + 4 * u)
                ImGui.ImDrawListAddTriangleFilled(dl, xe + sg * 10 * u, CYc - 6 * u, xe, CYc - 12 * u, xe, CYc, BONE)
            end
            T(CX, CYc + 38 * u, '> ' .. string.upper(tostring(tl or 'OBJECTIF')):sub(1, 30) .. string.format('   %d M', math.floor(dist + 0.5)), 11, RED_MID, 'c')
        end
    end
    end   -- boot >= 0.75

    -- ===== bas : bandeau du chat (seulement en direct) =====
    if d.banner then
        local BY = H - 86 * u
        R(M, BY, W - M, BY + 46 * u, SHADE)
        L(M, BY, W - M, BY, RED_DIM, 1); L(M, BY + 46 * u, W - M, BY + 46 * u, RED_DIM, 1)
        local vt = type(d.vote) == 'table' and d.vote or nil
        local x = M + 8 * u
        T(x, BY + 15 * u, 'VOTE', 14, BONE)
        x = x + 62 * u
        if vt and tonumber(vt.left) and tonumber(vt.tot) then
            T(x, BY + 15 * u, string.format('%d S', math.floor(vt.left + 0.5)), 13, RED)
            local nt = 30
            for i = 0, nt - 1 do
                local on = i < math.floor(nt * vt.left / vt.tot + 0.5)
                L(x + 50 * u + i * 6 * u, BY + 15 * u, x + 50 * u + i * 6 * u, BY + 31 * u, on and RED or RED_FAINT, 1.6)
            end
            local tx = W - M - 8 * u
            for k = math.min(#(vt.tally or {}), 3), 1, -1 do
                local row = vt.tally[k]
                local s = string.format('%s %d', tostring(row[1]), tonumber(row[2]) or 0)
                local wv = TW(s, 12 * u)
                T(tx, BY + 16 * u, s, 12, k == 1 and RED or RED_MID, 'r')
                for b = 0, math.min(8, (tonumber(row[2]) or 0)) - 1 do
                    R(tx - wv - 14 * u - b * 9 * u, BY + 18 * u, tx - wv - 8 * u - b * 9 * u, BY + 28 * u, k == 1 and RED or RED_MID)
                end
                tx = tx - wv - 30 * u - math.min(8, tonumber(row[2]) or 0) * 9 * u - 18 * u
            end
            x = x + 50 * u + nt * 6 * u + 24 * u
        else
            x = x + 10 * u
        end
        local btxt = tostring(d.banner):gsub('^VOTE[^:]*:%s*', '')
        T(x, BY + 16 * u, btxt:sub(1, 150), 12, RED_MID)
        if not vt and d.banner_last and tonumber(d.banner_last_t) and now - tonumber(d.banner_last_t) < 20 then
            T(W - M - 8 * u, BY + 16 * u, '> ' .. tostring(d.banner_last), 12, RED, 'r')
        end
    end

    -- ===== reperes de registre et reference =====
    for _, k in ipairs({ { 18 * u, 18 * u, 1, 1 }, { W - 18 * u, 18 * u, -1, 1 }, { 18 * u, H - 18 * u, 1, -1 }, { W - 18 * u, H - 18 * u, -1, -1 } }) do
        L(k[1], k[2], k[1] + k[3] * 16 * u, k[2], RED_DIM, 1); L(k[1], k[2], k[1], k[2] + k[4] * 16 * u, RED_DIM, 1)
    end
    T(CX, H - 30 * u, 'V-800  /  ANATOMIE DU SIGNAL  /  HUD REV. 02', 9, RED_DIM, 'c')
end
-- 19/10 V COMMENTE (Olivier : « V commente sur son HUD en gros ») : phrase du narrateur Python en grand en haut de l ecran,
-- machine a ecrire, 7 s, style T-800. Globale : pas d upvalue de plus.
function AgentDrawSay(d)
    if type(d) ~= 'table' or not d.say or not d.say_t then return end
    local age = os.time() - d.say_t
    if age < 0 or age > 7 then return end
    local okR, W, H = pcall(GetDisplayResolution)
    if not (okR and W and H and W > 0) then return end
    AgentSaySeen = AgentSaySeen or {}
    if AgentSaySeen.t ~= d.say_t then AgentSaySeen.t, AgentSaySeen.c0 = d.say_t, os.clock() end
    local txt = tostring(d.say)
    local n = math.min(#txt, math.floor((os.clock() - AgentSaySeen.c0) * 45) + 1)   -- machine a ecrire
    local shown = txt:sub(1, n) .. ((n < #txt and math.floor(os.clock() * 6) % 2 == 0) and '_' or '')
    local flags = 0
    for _, nm in ipairs({ 'NoTitleBar', 'NoResize', 'AlwaysAutoResize', 'NoCollapse', 'NoScrollbar', 'NoFocusOnAppearing', 'NoInputs', 'NoMove', 'NoSavedSettings' }) do
        local f = ImGuiWindowFlags[nm]
        if f then flags = (bit and bit.bor) and bit.bor(flags, f) or (flags + f) end
    end
    AgentSayW = AgentSayW or 700
    ImGui.SetNextWindowPos(math.max(0, (W - AgentSayW) / 2), H * 0.12, ImGuiCond.Always)
    ImGui.PushStyleColor(ImGuiCol.WindowBg, 0.04, 0.0, 0.0, 0.55)
    ImGui.PushStyleColor(ImGuiCol.Border, 0.95, 0.12, 0.08, 0.9)
    ImGui.PushStyleColor(ImGuiCol.Text, 1.0, 0.17, 0.10, 1.0)
    if ImGui.Begin('##agentsay', flags) then
        pcall(function() ImGui.SetWindowFontScale(2.2) end)
        ImGui.Text('> ' .. shown)
        pcall(function() local ww = ImGui.GetWindowSize(); if ww and ww > 50 then AgentSayW = ww end end)
    end
    ImGui.End()
    ImGui.PopStyleColor(3)
end
-- 19/10 TABLEAU DES SUCCES (Olivier : « 2 ») : total Steam + objectifs en cours, en haut a droite. Globale.
function AgentDrawGoals(d)
    if type(d) ~= 'table' or type(d.goals) ~= 'table' or #d.goals == 0 then return end
    local okR, W, H = pcall(GetDisplayResolution)
    if not (okR and W and H and W > 0) then return end
    local flags = 0
    for _, n in ipairs({ 'NoTitleBar', 'NoResize', 'AlwaysAutoResize', 'NoCollapse', 'NoScrollbar', 'NoFocusOnAppearing', 'NoInputs', 'NoMove', 'NoSavedSettings' }) do
        local f = ImGuiWindowFlags[n]
        if f then flags = (bit and bit.bor) and bit.bor(flags, f) or (flags + f) end
    end
    AgentGoalsW = AgentGoalsW or 300
    ImGui.SetNextWindowPos(math.max(0, W - AgentGoalsW - 20), H * 0.30, ImGuiCond.Always)
    ImGui.PushStyleColor(ImGuiCol.WindowBg, 0.05, 0.0, 0.0, 0.55)
    ImGui.PushStyleColor(ImGuiCol.Border, 0.95, 0.12, 0.08, 0.85)
    ImGui.PushStyleColor(ImGuiCol.Text, 1.0, 0.17, 0.10, 1.0)
    if ImGui.Begin('##agentgoals', flags) then
        for i, line in ipairs(d.goals) do
            if i > 6 then break end
            ImGui.Text((i == 1 and '' or '- ') .. tostring(line))
        end
        pcall(function() local ww = ImGui.GetWindowSize(); if ww and ww > 50 then AgentGoalsW = ww end end)
    end
    ImGui.End()
    ImGui.PopStyleColor(3)
end
-- 18/10 BANDEAU DU CHAT YOUTUBE (Olivier : « ou alors en bandeau ») : commandes du chat en bas de l ecran, visibles dans le
-- direct (le HUD est dessine dans l image du jeu) ; derniere commande acceptee 20 s. Globale : pas d upvalue de plus.
function AgentDrawBanner(d)
    if type(d) ~= 'table' or not d.banner then return end
    local okR, W, H = pcall(GetDisplayResolution)
    if not (okR and W and H and W > 0) then return end
    local flags = 0
    for _, n in ipairs({ 'NoTitleBar', 'NoResize', 'AlwaysAutoResize', 'NoCollapse', 'NoScrollbar', 'NoFocusOnAppearing', 'NoInputs', 'NoMove', 'NoSavedSettings' }) do
        local f = ImGuiWindowFlags[n]
        if f then flags = (bit and bit.bor) and bit.bor(flags, f) or (flags + f) end
    end
    AgentBannerW = AgentBannerW or 600
    ImGui.SetNextWindowPos(math.max(0, (W - AgentBannerW) / 2), H - 70, ImGuiCond.Always)
    ImGui.PushStyleColor(ImGuiCol.WindowBg, 0.05, 0.0, 0.0, 0.70)
    ImGui.PushStyleColor(ImGuiCol.Border, 0.95, 0.12, 0.08, 0.85)
    ImGui.PushStyleColor(ImGuiCol.Text, 1.0, 0.17, 0.10, 1.0)
    if ImGui.Begin('##agentbanner', flags) then
        ImGui.Text(tostring(d.banner))
        if d.banner_last and d.banner_last_t and os.time() - d.banner_last_t < 20 then
            ImGui.Text('> ' .. tostring(d.banner_last))
        end
        pcall(function() local ww = ImGui.GetWindowSize(); if ww and ww > 50 then AgentBannerW = ww end end)
    end
    ImGui.End()
    ImGui.PopStyleColor(3)
end
local function drawHud()
    if not hudShow or not lastExport then return end
    hudRead()
    if lastExport.menu or ((lastExport.breach or {}).state == 1) then return end   -- menus plein ecran, Breach Protocol (clics)
    if AgentHudStyle == 'v800' and not AgentV8Failed then   -- 19/10 : HUD « Anatomie du Signal » (repli auto sur l ancien)
        local okV, errV = pcall(AgentDrawV800, lastExport, hudData, hudKills)
        if okV then return end
        AgentV8Failed = true
        journal('HUD V-800 indisponible, retour a l ancien HUD : ' .. tostring(errV))
    end
    local nA, hpA = 0, tonumber(lastExport.hp) or 100
    for _, en in ipairs(lastExport.enemies or {}) do if not en.dead and not en.police then nA = nA + 1 end end
    local pulse = (not lastExport.dead) and (hpA < 30 or nA >= 6) and (math.floor(os.clock() * 4) % 2 == 0)
    local bgR, bgA = (pulse and 0.32 or 0.05), (pulse and 0.78 or 0.62)
    local colors = {
        { ImGuiCol.WindowBg, bgR, 0.0, 0.0, bgA }, { ImGuiCol.Border, 0.95, 0.12, 0.08, 0.85 },
        { ImGuiCol.Text, 1.0, 0.17, 0.10, 1.0 }, { ImGuiCol.Separator, 0.8, 0.1, 0.06, 0.8 },
        { ImGuiCol.PlotHistogram, 1.0, 0.14, 0.08, 1.0 }, { ImGuiCol.FrameBg, 0.25, 0.02, 0.02, 0.8 },
    }
    local nc, nv = 0, 0
    for _, c in ipairs(colors) do
        if c[1] ~= nil then ImGui.PushStyleColor(c[1], c[2], c[3], c[4], c[5]); nc = nc + 1 end
    end
    if ImGuiStyleVar.WindowRounding then ImGui.PushStyleVar(ImGuiStyleVar.WindowRounding, 0.0); nv = nv + 1 end
    if ImGuiStyleVar.WindowBorderSize then ImGui.PushStyleVar(ImGuiStyleVar.WindowBorderSize, 1.0); nv = nv + 1 end
    local okW, errW = pcall(function()
        local okR, W, H = pcall(GetDisplayResolution)
        local x, y = nil, nil
        if hudPos ~= 'libre' and okR and W and H and W > 0 then
            local m = 40
            if hudPos == 'milieu_droite' then x, y = W - hudW - m, H * 0.58 - hudH / 2
            elseif hudPos == 'milieu_gauche' then x, y = m, H * 0.5 - hudH / 2
            elseif hudPos == 'bas_gauche' then x, y = m, H - hudH - 190
            elseif hudPos == 'bas_droite' then x, y = W - hudW - m, H - hudH - 190 end
        end
        if x then ImGui.SetNextWindowPos(math.max(0, x), math.max(0, y), ImGuiCond.Always)
        else ImGui.SetNextWindowPos(40, 300, ImGuiCond.FirstUseEver) end
        if ImGui.Begin('V-800##agenthud', hudFlags()) then
            local okB, errB = pcall(hudBody)
            if not okB and tostring(errB) ~= hudLastErr then hudLastErr = tostring(errB); journal('HUD erreur : ' .. hudLastErr) end
            pcall(function() local ww, hh = ImGui.GetWindowSize(); if ww and ww > 50 then hudW, hudH = ww, hh end end)
        end
        ImGui.End()
    end)
    if not okW and tostring(errW) ~= hudLastErr then hudLastErr = tostring(errW); journal('HUD fenetre : ' .. hudLastErr) end
    if nv > 0 then ImGui.PopStyleVar(nv) end
    if nc > 0 then ImGui.PopStyleColor(nc) end
    local okL, errL = pcall(drawLocks)           -- apres les styles du HUD : les cadres ont les leurs
    pcall(AgentDrawBanner, hudData)              -- 18/10 : bandeau des commandes du chat YouTube
    pcall(AgentDrawSay, hudData)                 -- 19/10 : V commente ses decisions en grand
    pcall(AgentDrawGoals, hudData)               -- 19/10 : tableau des succes (haut droite)
    if not okL and tostring(errL) ~= hudLastErr then hudLastErr = tostring(errL); journal('HUD cadres : ' .. hudLastErr) end
end
registerHotkey('agent_hud_toggle', 'Afficher / masquer le HUD de V', function() hudShow = not hudShow end)
registerHotkey('agent_hud_color', 'HUD de V-800 : bleu / rouge', function()
    AgentHudColor = (AgentHudColor == 'rouge') and 'bleu' or 'rouge'
end)
registerHotkey('agent_hud_style', 'HUD de V : V-800 (nouveau) / classique', function()
    AgentHudStyle = (AgentHudStyle == 'v800') and 'classique' or 'v800'; AgentV8Failed = false
end)
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
    local okH, errH = pcall(drawHud)
    if not okH and tostring(errH) ~= hudLastErr then hudLastErr = tostring(errH); journal('HUD : ' .. hudLastErr) end
    if not ui.open then return end
    if ImGui.Begin('CyberpunkAgent') then
        pcall(hudRead)
        pcall(uiSyncEffective)
        local cfgE = hudData and hudData.cfg
        if cfgE then
            local live = (not hudData.ended) and tonumber(hudData.ts) and (os.time() - tonumber(hudData.ts) <= 8)
            ImGui.TextWrapped('Agent ' .. (live and '(en cours)' or '(derniere session)') .. ' : ' .. tostring(cfgE))
        else
            ImGui.TextWrapped('Agent : reglages effectifs inconnus (pas encore lance).')
        end
        ImGui.TextWrapped('Seuls les reglages MODIFIES ici puis Enregistrer s appliquent (au prochain lancement) ; ils l emportent sur le panneau Windows tant qu on ne les y change pas.')
        ImGui.Separator()
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
        ui.sprint = ImGui.Checkbox('Sprinter sans cesse en combat (regeneration)', ui.sprint)
        ui.steal = ImGui.Checkbox('Voler une voiture arretee si besoin ou par envie', ui.steal)
        ui.terminals = ImGui.Checkbox('Se connecter aux points d acces (Breach Protocol)', ui.terminals)
        ui.fasttravel = ImGui.Checkbox('Voyage rapide (bornes)', ui.fasttravel)
        ui.phone = ImGui.Checkbox('Repondre aux appels', ui.phone)
        ui.sms = ImGui.Checkbox('Lire et repondre aux SMS', ui.sms)
        ui.appearance = ImGui.Checkbox('Changer d apparence au miroir de temps en temps', ui.appearance)
        ui.recipes = ImGui.Checkbox('Acheter et apprendre des plans de craft', ui.recipes)
        hudShow = ImGui.Checkbox('HUD Terminator (statut de V a l ecran)', hudShow)
        hudLocks = ImGui.Checkbox('Cadres de verrouillage sur les hostiles', hudLocks)
        ImGui.Text('Position du HUD :')
        for i, p in ipairs(hudPosList) do
            if ImGui.RadioButton(hudPosLabels[i] .. '##hudpos', hudPos == p) then hudPos = p end
            if i < #hudPosList then ImGui.SameLine() end
        end
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
        ImGui.Separator()
        ImGui.Text('Diriger V (prise en compte au prochain cycle, quelques secondes)')
        ui.directive = ImGui.InputText('Directive', ui.directive, 200)
        if ImGui.Button('Envoyer') and ui.directive ~= '' then
            pendingDirective = ui.directive
            ui.directiveSent = 'envoyee (' .. ui.directive .. ') ' .. os.date('%H:%M:%S')
            ui.directive = ''
        end
        ImGui.SameLine(); ImGui.Text(ui.directiveSent)
        ImGui.TextWrapped('Commandes : stop, pause, reprendre, attaque, objectif, marchand, charcudoc, explore, changer_quete, va_a <lieu>')
        ImGui.Separator()
        if ImGui.Button('Enregistrer') then pcall(uiSave) end
        ImGui.SameLine()
        if ImGui.Button('Oublier les reglages in-game') then pcall(uiForget) end
        ImGui.Text(ui.saved)
        ImGui.Text('Lance ensuite CyberpunkAgent.exe (F11 pause, F12 arret).')
    end
    ImGui.End()
end)

registerForEvent('onShutdown', function()
    if fh then fh:close(); fh = nil end
end)
