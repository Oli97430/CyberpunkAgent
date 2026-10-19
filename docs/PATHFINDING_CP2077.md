# Pathfinding dans Cyberpunk 2077 : référence pour CyberpunkAgent

Source : dump en jeu (CET `DumpType` + Codeware `Reflection`) de la version installée, 19/10/2026.
Dumps bruts : `dumps/nav_dump.txt` (496 types de script) et `dumps/nav_dump2.txt` (269 types natifs).

Légende : **[sig]** = signature issue du dump (certaine) · **[sem]** = sens supposé, à vérifier en jeu.

---

## 1. Ce que fait ton mod aujourd'hui

- `CyberpunkAgent.exe` (Python, cerveau LLM) → `AgentProbe` (Lua CET) via SQLite/`cmd.json`, commandes `path_to` / `path_to_quest`.
- Le Lua calcule le chemin (`computePath`, `init.lua` ~l.2276) → `path.json` (points, longueur, `partial`) ; **le Python suit les points en simulant les commandes**.
- Enchaînement : `FindPointInSphereForCharacter(pos, 3)` (départ) → `FindPointInSphereForCharacter(cible, 6)` (arrivée) → `CalculatePathForCharacter(a, b, 2.0, player)` ; en cas d'échec, échantillonnage de points intermédiaires (80…20 m, ±30 m de côté) et filtrage de la zone `avoid`.

Problèmes relevés dans les journaux :
1. **« départ hors navmesh »** (28 fois dans `agent_log.txt`) : l'accrochage du point de départ échoue.
2. **« bloqué »** : V reste coincé en suivant le chemin (ex. (-931, 1203), 393 m restants, plusieurs essais).

Important : V **n'est pas un agent IA**. Les commandes de type `AIMoveToCommand` (§5) déplacent des PNJ, pas le joueur. Pour V, le jeu fournit le chemin ; le suivre reste ton travail (ton Python).

---

## 2. Systèmes et accès depuis CET

| Accès Lua | Classe native | Rôle |
|---|---|---|
| `Game.GetAINavigationSystem()` | `AINavigationSystem` | requêtes par personnage (tient compte de sa taille et de ses réglages) |
| `Game.GetNavigationSystem()` | `worldNavigationScriptInterface` | requêtes « navmesh humain » + **obstacles dynamiques** + poursuite |
| `Game.GetSpatialQueriesSystem()` | `gameSpatialQueriesSystem` | **raycasts / overlaps** physiques (géométrie réelle, voitures, caisses) |

---

## 3. API complète

### 3.1 `AINavigationSystem` (par personnage)

| Fonction [sig] | Renvoie | Notes |
|---|---|---|
| `CalculatePathForCharacter(startPoint: Vector4, endPoint: Vector4, findPointTolerance: Float, owner: entEntity, [opt] costModCircle: worldNavigationScriptCostModCircle)` | `worldNavigationScriptPath` (nil si aucun) | **`costModCircle` = zone d'évitement native**, inutilisée pour l'instant |
| `FindPointInSphereForCharacter(center: Vector4, radius: Float, owner: entEntity)` | `worldNavigationScriptFindPointResult` | ce que tu utilises |
| `FindPointInBoxForCharacter(center: Vector4, extents: Vector4, owner: entEntity)` | `FindPointResult` | boîte : utile pour une recherche à plat (XY large, Z faible) |
| `FindPointBelowForCharacter(origin: Vector4, querySphereRadius: Float, numberOfSpheres: Int32, character: entEntity)` | `FindPointResult` (+ surcharge avec `resultingZOffset: Float`) | **cherche vers le bas** : escaliers, rebords, V légèrement au-dessus du maillage |
| `GetNearestNavmeshPointBelow(character: entEntity, origin: Vector4, querySphereRadius: Float, numberOfSpheres: Int32)` | `Vector4` | idem, renvoie directement le point |
| `IsPointOnNavmesh(character, point: Vector4, [opt] tolerance: Float)` | `Bool` | 3 surcharges ; l'une renvoie **`navmeshPoint: Vector4`** (`tolerance: Vector4`) |
| `FindWallInLineForCharacter(startPoint: Vector4, endPoint: Vector4, findPointTolerance: Float, owner: entEntity)` | `worldNavigationScriptFindWallResult` | **un segment droit est-il praticable ?** |
| `HasPathForward(sourceObject: gameObject, distance: Float)` | `Bool` | [sem] peut-il avancer de `distance` m dans sa direction |
| `GetFurthestNavmeshPointInFront(origin: entEntity, querySphereRadius: Float, numberOfSpheres: Int32, [opt] offsetFromOrigin: Vector4, [opt] checkPathToOrigin: Bool, [opt] ratioCurveName: CName)` | `(Bool, point: Vector4)` | point atteignable le plus loin droit devant |
| `GetFurthestNavmeshPointBehind(…)` / `GetNearestNavmeshPointBehind(origin, radius, nbSpheres, [opt] checkPathToOrigin)` | `(Bool, point)` | **se décoincer en reculant** |
| `TryToFindNavmeshPointAroundPoint(owner, originPosition: Vector4, originOrientation: Quaternion, probeDimensions: Vector4, numberOfSpheres: Int32, sphereDistanceFromOrigin: Float, [opt] checkPathToOrigin: Bool)` | `(Bool, point)` | recherche en couronne autour d'un point |
| `StartPathfinding(query: AINavigationSystemQuery)` | `Uint32` (id) | pathfinding **asynchrone** |
| `GetResult(id: Uint32, result: AINavigationSystemResult)` | `Bool` (prêt ?) | à interroger aux ticks suivants |
| `StopPathfinding(id: Uint32)` | `Bool` | |

### 3.2 `worldNavigationScriptInterface` (`Game.GetNavigationSystem()`)

| Fonction [sig] | Renvoie | Notes |
|---|---|---|
| `CalculatePathOnlyHumanNavmesh(startPoint, endPoint, agentSize: NavGenAgentSize, findPointTolerance: Float, [opt] costModCircle)` | `worldNavigationScriptPath` | comme ci-dessus, sans entité |
| `FindPointInSphereOnlyHumanNavmesh(center, radius, agentSize, heightDetail: Bool)` | `FindPointResult` | |
| `GetNearestNavmeshPointBelowOnlyHumanNavmesh(origin, querySphereRadius, numberOfSpheres)` | `Vector4` | |
| `FindWallInLineOnlyHumanNavmesh(startPoint, endPoint, agentSize, findPointTolerance)` | `FindWallResult` | |
| **`AddObstacle(position: Vector4, radius: Float, height: Float, agentSize: NavGenAgentSize)`** | `worldNavigationScriptObstacle` | **obstacle dynamique** : le pathfinding le contourne |
| **`RemoveObstacle(obstacle)`** | — | toujours retirer ce qu'on ajoute |
| `IsNavmeshStreamedInLocation(origin, findPointTolerance, [opt] allowedMask: Uint16, [opt] blockedMask: Uint16)` | `Bool` | navmesh chargé à cet endroit ? (chargé par secteurs) |
| `IsOnGround(target: gameObject, [opt] queryLength: Float)` | `Bool` | V au sol (ou en saut, en chute, sur un rebord) |
| `FindNavmeshPointAwayFromReferencePoint(pos, refPos, distance, agentSize, [opt] distanceTolerance, [opt] angleTolerance)` | `(Bool, destination)` | s'éloigner d'un point (fuir, se décoincer) |
| `FindPursuitPoint(playerPos, pos, dir, radius, navVisCheck: Bool, agentSize)` / `…Range` / `…PointsRange(…, count)` | `(Bool, destination, isDestinationFallback)` | points autour d'une cible (poursuite de PNJ) |

### 3.3 Types de données

| Type | Champs |
|---|---|
| `worldNavigationScriptPath` | `path: array<Vector4>` · `CalculateLength() → Float` |
| `worldNavigationScriptFindPointResult` | `status: worldNavigationRequestStatus` · `point: Vector4` |
| `worldNavigationScriptFindWallResult` | `status` · `isHit: Bool` · `hitPosition: Vector4` |
| `worldNavigationScriptCostModCircle` | `pos: Vector4` · `range: Float` · `cost: Float` |
| `AINavigationSystemQuery` | `source: AIPositionSpec` · `target: AIPositionSpec` · `allowedTags / blockedTags: array<CName>` · `minDesiredDistance / maxDesiredDistance: Float` · `useFollowSlots / usePredictionTime: Bool` |
| `AINavigationSystemResult` | `hasFailed` · `hasPath` · `hasClosestPointOnNavmesh` · `hasClosestReachablePoint` · `lastSourcePosition` · `lastTargetPosition` · `adjustedTargetPosition` · **`closestPointOnNavmesh`** · **`closestReachablePoint`** (tous `WorldPosition`) |
| `AIPositionSpec` | `entity` · `worldPosition` · statiques `SetEntity`, `SetWorldPosition(spec, WorldPosition)`, `GetWorldPosition` |
| `WorldPosition` | statiques `SetVector4(wp, v)`, `SetXYZ`, `ToVector4(wp)` |

### 3.4 Énumérations (valeurs issues du dump)

- `worldNavigationRequestStatus` : `OK=0`, `InvalidStartingPosition=1`, `InvalidEndPosition=2`, `OtherError=3`
- `NavGenAgentSize` : `Human=0`, `Vehicle=1`
- `worldOffMeshConnectionType` (liens hors maillage) : `Door=0`, `Ladder=1`, `Floor=2`, `Jump=3`, `Elevator=4`, `Drone=5`, `Exploration=6`, `Custom=7`, `Blockade=8`
- `NavGenNavmeshImpact` (types de zones) : `Walkable=0`, `Ignored=1`, `Blocking=2`, `Road=3`, `Stairs=4`, `Drones=5`, `Terrain=6`
- `moveMovementType` : `Walk=0`, `Run=1`, `Sprint=2`, `Strafe=3`, `Stand=4`

### 3.5 Raycasts physiques (`gameSpatialQueriesSystem`)

| Fonction [sig] | Renvoie |
|---|---|
| `SyncRaycastByCollisionGroup(start: Vector4, end: Vector4, [opt] collisionGroup: CName, [opt] staticOnly: Bool, [opt] dynamicOnly: Bool)` | `(Bool touché, result: physicsTraceResult)` |
| `SyncRaycastByQueryPreset(start, end, [opt] queryPreset: CName, [opt] staticOnly, [opt] dynamicOnly)` | idem |
| `SyncRaycastByCollisionPreset(…)`, `SyncRaycastByQueryFilter(start, end, queryFilter: physicsQueryFilter, …)` | idem |
| `Overlap(primitiveDimension: Vector4, position: Vector4, rotation: EulerAngles, [opt] collisionGroup)` | `(Bool, result)` : **« y a-t-il quelque chose dans cette boîte »** |
| `GetPlayerObstacleSystem()` | `gamePlayerObstacleSystem` (non dumpé : à explorer) |

`physicsTraceResult` : `position: Vector3`, `normal: Vector3`, `material: CName`, statiques `IsValid(r)`, **`GetHitEntity(r)`** (ce qu'on a touché : porte, véhicule, PNJ…).
`physicsQueryFilter` : statiques `ALL()`, `ZERO()`, `AddGroup(group)`.

---

## 4. Corrections pour tes deux problèmes

> Les extraits suivent le style d'`AgentProbe` (pcall partout). **Pas encore testés en jeu** : les constructeurs (`NewObject`, `.new()`) sont les points les plus probables à ajuster.

### 4.1 « départ hors navmesh » : une chaîne de secours au lieu d'un seul essai

Aujourd'hui : un seul `FindPointInSphereForCharacter(pos, 3.0)`. Cas qui échouent : escaliers, rebords, V quelques cm au-dessus du maillage, V en l'air, navmesh pas encore chargé.

```lua
-- renvoie un point du navmesh sous/près de V, ou nil + raison
local function snapStart(player)
    local ai, ns = Game.GetAINavigationSystem(), Game.GetNavigationSystem()
    local pos = player:GetWorldPosition()
    -- 0) navmesh pas chargé ici : ATTENDRE (réessayer plus tard) plutôt qu'échouer
    if not ns:IsNavmeshStreamedInLocation(pos, 2.0) then return nil, 'navmesh non streame' end
    -- 1) V en l'air (saut, chute, rebord) : attendre que V soit au sol
    local okG, ground = pcall(function() return ns:IsOnGround(player, 2.0) end)
    if okG and ground == false then return nil, 'V pas au sol' end
    -- 2) recherche habituelle (petit rayon)
    local r = ai:FindPointInSphereForCharacter(pos, 3.0, player)
    if r and r.point and tostring(r.status):find('OK') then return r.point end
    -- 3) recherche VERS LE BAS (escaliers, rebords) : 4 sphères de 1,5 m
    local okB, below = pcall(function() return ai:GetNearestNavmeshPointBelow(player, pos, 1.5, 4) end)
    if okB and below and (below.x ~= 0 or below.y ~= 0) then return below end
    -- 4) recherche dans une boîte plate (XY large, Z faible : évite de choisir un autre étage)
    local okX, bx = pcall(function() return ai:FindPointInBoxForCharacter(pos, Vector4.new(6, 6, 1.5, 0), player) end)
    if okX and bx and bx.point and tostring(bx.status):find('OK') then return bx.point end
    return nil, 'depart hors navmesh (4 methodes)'
end
```

Distinguer les trois raisons d'échec (`non streamé`, `pas au sol`, `hors navmesh`) permet au Python de réagir correctement : attendre, attendre, ou **reculer** (`GetNearestNavmeshPointBehind`).

### 4.2 Zones bloquantes : évitement natif plutôt que filtrage après coup

Aujourd'hui : les chemins candidats sont écartés s'ils traversent `avoid` (6 m). Deux options natives :

**a) Cercle de coût (par requête)** : le pathfinding rend la zone plus chère et la contourne si un détour existe.
```lua
local okC, circle = pcall(function() return NewObject('worldNavigationScriptCostModCircle') end)
if okC and circle then
    circle.pos = Vector4.new(avoid.x, avoid.y, pos.z, 1.0)
    circle.range = 8.0
    circle.cost = 100.0          -- [sem] multiplicateur de coût : à calibrer (10, 100, 1000)
end
local path = ai:CalculatePathForCharacter(a.point, b.point, 2.0, player, circle)
```

**b) Obstacle persistant (pour un endroit connu comme bloqué)** : valable pour toutes les requêtes suivantes, jusqu'à son retrait.
```lua
local ns = Game.GetNavigationSystem()
local obst = ns:AddObstacle(Vector4.new(-931, 1203, z, 1.0), 4.0, 2.0, NavGenAgentSize.Human)
-- ... garder `obst` dans une table ; ns:RemoveObstacle(obst) au changement de zone / après N minutes
```
Recommandation : (b) pour tes « zones bloquantes mémorisées » (`brain_log` : `zone bloquante memorisee (-931,1203)`), (a) pour un détour ponctuel.

### 4.3 « bloqué » en suivant : vérifier les segments avant de les parcourir

Le navmesh ne connaît pas tout : voitures garées, caisses, PNJ, portes fermées. Pour chaque segment `[p_i, p_{i+1}]` du chemin :

```lua
local function segmentBlocked(player, p1, p2)
    -- 1) navmesh : un mur sur la ligne droite ? (le chemin peut couper les angles une fois lissé)
    local w = Game.GetAINavigationSystem():FindWallInLineForCharacter(p1, p2, 0.5, player)
    if w and w.isHit then return 'mur', w.hitPosition end
    -- 2) physique : quelque chose à hauteur de genou et de poitrine (objets dynamiques compris)
    local sq = Game.GetSpatialQueriesSystem()
    for _, h in ipairs({ 0.4, 1.2 }) do
        local a = Vector4.new(p1.x, p1.y, p1.z + h, 1.0)
        local b = Vector4.new(p2.x, p2.y, p2.z + h, 1.0)
        local hit, res = sq:SyncRaycastByCollisionGroup(a, b, 'Static', false, false)
        if hit then
            local ent = nil; pcall(function() ent = res:GetHitEntity() end)
            return 'obstacle', res.position, ent      -- ent : porte ? véhicule ? -> action spécifique
        end
    end
    return nil
end
```

Ensuite :
- `mur` → recalculer depuis la position actuelle de V (le chemin était périmé) ;
- `obstacle` avec une entité **porte** → ta logique de portes existante (`lastDoors`) ;
- `obstacle` avec un **véhicule** → `AddObstacle` sur sa position + recalcul ;
- rien trouvé mais V n'avance pas → `GetNearestNavmeshPointBehind(player, 2.0, 3, true)`, reculer, puis recalculer.

`collisionGroup` : `'Static'` est une supposition ; essaie aussi `'Dynamic'`, `'Vehicle'`, ou les presets via `SyncRaycastByQueryPreset(a, b, 'World Static')`. Les noms disponibles sont dans les fichiers de physique du jeu (à confirmer en jeu).

### 4.4 Chemins partiels : laisser le jeu donner le « point atteignable le plus proche »

Aujourd'hui : jusqu'à 25 échantillons (5 distances × 5 décalages latéraux), chacun avec un `CalculatePathForCharacter`, le tout en synchrone. Le pathfinding **asynchrone** renvoie nativement `closestReachablePoint` quand la cible est inaccessible :

```lua
local q = AINavigationSystemQuery.new()
local function spec(v) local s = AIPositionSpec.new(); local wp = WorldPosition.new()
    WorldPosition.SetVector4(wp, v); AIPositionSpec.SetWorldPosition(s, wp); return s end
q.source = spec(a.point); q.target = spec(target)
local id = Game.GetAINavigationSystem():StartPathfinding(q)
-- ticks suivants (onUpdate) :
local res = AINavigationSystemResult.new()
if Game.GetAINavigationSystem():GetResult(id, res) then
    if res.hasPath then -- chemin complet : le recalculer avec CalculatePathForCharacter pour avoir les points
    elseif res.hasClosestReachablePoint then
        local mid = WorldPosition.ToVector4(res.closestReachablePoint)   -- cible intermédiaire, choisie par le jeu
    end
end
```
[sem] `AINavigationSystemResult` ne contient pas de liste de points : s'en servir pour **choisir** la cible intermédiaire, puis `CalculatePathForCharacter` pour les points. `GetResult` prend un `script_ref` : depuis CET, vérifier que `res` est bien rempli (sinon passer par un petit wrapper redscript via Codeware).

### 4.5 Ce que le chemin ne te dit PAS

- `worldNavigationScriptPath` = **des points seulement**. Aucune info sur les **liens hors maillage** (porte, échelle, saut, ascenseur : `worldOffMeshConnectionType`). Un chemin peut passer par une porte ou un ascenseur : avancer tout droit ne fera pas passer V.
- Détection pratique : points proches d'une entité porte/ascenseur (`GetHitEntity` d'un raycast, ou tes listes `doors` / ascenseurs) → action spécifique (ouvrir, `GoToFloor`).
- Le navmesh est **chargé par secteurs** : les longs chemins (> ~60–100 m) échouent souvent. C'est ce que tu gères avec `partial` ; `IsNavmeshStreamedInLocation(cible)` te dit à l'avance si la cible est chargée.

---

## 5. Déplacement des PNJ (inutilisable sur V)

Pour déplacer un **PNJ** (compagnon, test), pas le joueur :

```lua
local cmd = AIMoveToCommand.new()
local s = AIPositionSpec.new(); local wp = WorldPosition.new()
WorldPosition.SetVector4(wp, destination); AIPositionSpec.SetWorldPosition(s, wp)
cmd.movementTarget = s
cmd.movementType = moveMovementType.Run          -- Walk / Run / Sprint / Strafe / Stand
cmd.desiredDistanceFromTarget = 1.0
cmd.finishWhenDestinationReached = true
cmd.ignoreNavigation = false
npc:GetAIControllerComponent():SendCommand(cmd)
```

Disponibles aussi (à dumper en détail si besoin) : `AIRotateToCommand`, `AIFollowTargetCommand`, `AITeleportCommand`, `AIMoveOnSplineCommand`, `AIUseWorkspotCommand`, `AIMoveToCoverCommand`.

---

## 6. À vérifier en jeu (par priorité)

1. `NewObject('worldNavigationScriptCostModCircle')` et l'échelle de `cost`.
2. Le nom du bon `collisionGroup` / `queryPreset` de raycast pour voir les caisses et les voitures.
3. `GetResult(id, res)` depuis CET (argument `script_ref`).
4. `GetPlayerObstacleSystem()` : inexploré, peut-être un détecteur d'obstacles dédié au joueur.
5. `HasPathForward(player, 3.0)` : test peu coûteux de « V peut-il avancer ? ».

Le système de sondes d'`AgentProbe` (`addProbe`) est le bon endroit pour tester ces 5 points en une seule session.
