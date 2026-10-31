-- vrPoseDump: one-off diagnostic for BeamNG.drive 0.40.x VR controller poses.
--
-- Usage (GE Lua console, "~" key, top-left dropdown set to GE):
--   extensions.load('vrPoseDump')
--   extensions.vrPoseDump.start()        -- 10 s countdown, then 25 s guided capture
--   extensions.vrPoseDump.start(5, 25)   -- custom countdown / capture length
--   extensions.vrPoseDump.stop()         -- end early (still writes files)
--
-- Everything is read-only except a pass-through wrapper on the two OpenXR camera
-- functions while the capture runs. It is removed again when the capture ends.
-- Output goes to <BeamNG user folder>/vrPoseDump/<timestamp>/.

local M = {}

-- LuaJIT is 5.1: no guaranteed table.pack/table.unpack.
local function pack(...) return {n = select('#', ...), ...} end
local unpack = table.unpack or unpack

local outDir
local capture          -- nil when idle
local wrapped = {}     -- name -> original function
local lastSetter, lastGetter, setterCalls, getterCalls = nil, nil, 0, 0

-- ---------------------------------------------------------------- helpers ---

local function log(msg)
  print('[vrPoseDump] ' .. msg)
end

local function toast(msg, ttl)
  pcall(ui_message, msg, ttl or 5, 'vrPoseDump')
  log(msg)
end

-- Wall-clock seconds; os.clock() is CPU time and drifts in a multithreaded game.
local okSocket, socket = pcall(require, 'socket')
local function now()
  if okSocket and socket and socket.gettime then return socket.gettime() end
  return os.clock()
end

-- Turns anything (cdata vec3/quat, userdata, nested tables) into plain JSON-able data.
local function plain(v, depth, seen)
  depth, seen = depth or 0, seen or {}
  local t = type(v)
  if t == 'nil' or t == 'boolean' or t == 'string' then return v end
  if t == 'number' then
    if v ~= v or v == math.huge or v == -math.huge then return tostring(v) end
    return v
  end
  if t == 'function' then return '<function>' end
  if t == 'cdata' or t == 'userdata' then
    local out = {}
    for _, k in ipairs({'x', 'y', 'z', 'w'}) do
      local ok, c = pcall(function() return v[k] end)
      if ok and type(c) == 'number' then out[k] = c end
    end
    if next(out) then return out end
    return '<' .. t .. ' ' .. tostring(v) .. '>'
  end
  if t == 'table' then
    if seen[v] then return '<cycle>' end
    if depth > 6 then return '<depth>' end
    seen[v] = true
    local out = {}
    for k, c in pairs(v) do out[tostring(k)] = plain(c, depth + 1, seen) end
    seen[v] = nil
    return out
  end
  return '<' .. t .. '>'
end

local function call(fn, ...)
  if type(fn) ~= 'function' then return {ok = false, err = 'not a function'} end
  local r = pack(pcall(fn, ...))
  if not r[1] then return {ok = false, err = tostring(r[2])} end
  local vals = {}
  for i = 2, r.n do vals[#vals + 1] = plain(r[i]) end
  return {ok = true, n = r.n - 1, values = vals}
end

local function write(name, text)
  local path = outDir .. name
  local ok = writeFile(path, text)
  if not ok then log('FAILED to write ' .. path) end
  return ok
end

local function writeJson(name, data)
  local ok, text = pcall(jsonEncodePretty or jsonEncode, data)
  if not ok then ok, text = pcall(jsonEncode, data) end
  write(name, ok and text or ('json encode failed: ' .. tostring(text)))
end

local function sortedKeys(t)
  local keys = {}
  for k in pairs(t) do keys[#keys + 1] = tostring(k) end
  table.sort(keys)
  return keys
end

-- Lists every key of a table, its metatable and a table __index.
local function describe(obj)
  local d = {type = type(obj), keys = {}, meta = {}, index = {}}
  if type(obj) == 'table' then
    for _, k in ipairs(sortedKeys(obj)) do d.keys[k] = type(obj[k]) end
  end
  local mt = getmetatable(obj)
  if type(mt) == 'table' then
    for _, k in ipairs(sortedKeys(mt)) do d.meta[k] = type(mt[k]) end
    if type(mt.__index) == 'table' then
      for _, k in ipairs(sortedKeys(mt.__index)) do d.index[k] = type(mt.__index[k]) end
    end
  end
  return d
end

-- ----------------------------------------------------------- static probe ---

local function probeApi()
  local api = {
    version = rawget(_G, 'beamng_versiond') or rawget(_G, 'beamng_versionb'),
    buildType = rawget(_G, 'beamng_buildtype'),
    arch = rawget(_G, 'beamng_arch'),
    time = os.date('%Y-%m-%d %H:%M:%S'),
    OpenXR = describe(rawget(_G, 'OpenXR')),
    interestingGlobals = {},
    loadedExtensions = {},
  }

  -- Globals whose names look VR related.
  for _, k in ipairs(sortedKeys(_G)) do
    local lk = k:lower()
    if lk:find('xr') or lk:find('vr') or lk:find('hmd') or lk:find('headset')
      or lk:find('hand') or lk:find('pose') or lk:find('tracked') then
      api.interestingGlobals[k] = describe(_G[k])
    end
  end

  -- Loaded GE extensions (names only) and any VR-looking ones in full.
  if type(extensions) == 'table' then
    for _, k in ipairs(sortedKeys(extensions)) do
      if type(extensions[k]) == 'table' then
        api.loadedExtensions[#api.loadedExtensions + 1] = k
        local lk = k:lower()
        if lk:find('xr') or lk:find('vr') then api.interestingGlobals['extensions.' .. k] = describe(extensions[k]) end
      end
    end
  end

  -- Camera helpers we may compose with.
  api.camera = {
    core_camera = describe(rawget(_G, 'core_camera')),
    getCameraPosition = call(rawget(_G, 'getCameraPosition')),
    getCameraQuat = call(rawget(_G, 'getCameraQuat')),
    activeCam = call(core_camera and core_camera.getActiveCamName),
  }

  -- OpenXR related settings.
  api.settings = {}
  local ok, all = pcall(function() return settings.getValues and settings.getValues() end)
  if ok and type(all) == 'table' then
    for k, v in pairs(all) do
      if tostring(k):lower():find('xr') or tostring(k):lower():find('vr') then api.settings[tostring(k)] = plain(v) end
    end
  else
    for _, k in ipairs({'openXRuseControllers', 'openXRhandPoseMultiplier', 'openXRhandPoseDirectionDegrees',
      'openXRfreeCenter', 'openXRsnapTurnUnicycle', 'openXRuiMode', 'openXRapidumpEnabled', 'openXRdebugEnabled'}) do
      local okV, v = pcall(settings.getValue, k)
      api.settings[k] = okV and plain(v) or '<error>'
    end
  end

  writeJson('api.json', api)
  return api
end

-- Calls zero-argument OpenXR getters one at a time. The name is written to disk
-- before each call so a native crash still tells us which one did it.
local skip = {getSourcePoseStates = true}
local function probeGetters()
  local xr = rawget(_G, 'OpenXR')
  if type(xr) ~= 'table' then return end
  local names = {}
  local function collect(t) for k, v in pairs(t) do if type(v) == 'function' then names[tostring(k)] = true end end end
  collect(xr)
  local mt = getmetatable(xr)
  if type(mt) == 'table' and type(mt.__index) == 'table' then collect(mt.__index) end

  local results, order = {}, sortedKeys(names)
  for _, k in ipairs(order) do
    if (k:match('^get') or k:match('^is') or k:match('^has')) and not skip[k] then
      write('getter_probe_in_progress.txt', 'calling OpenXR.' .. k .. '()\n')
      results[k] = call(xr[k])
    end
  end
  write('getter_probe_in_progress.txt', 'done\n')
  writeJson('getters.json', results)
end

-- Copies every game Lua file that mentions OpenXR, plus a list of all VR-ish paths.
local function copySources()
  local files = FS:findFiles('/lua/', '*.lua', -1, true, false) or {}
  local hits, vrPaths = {}, {}
  for _, path in ipairs(files) do
    local lp = path:lower()
    if lp:find('xr') or lp:find('vr') then vrPaths[#vrPaths + 1] = path end
    local text = readFile(path)
    if text then
      local lt = text:lower()
      if lt:find('openxr', 1, true) or lt:find('sourceposestate', 1, true) or lt:find('inputsourcestate', 1, true) then
        hits[#hits + 1] = path
        write('sources' .. path, text)
      end
    end
  end
  table.sort(hits); table.sort(vrPaths)
  writeJson('sources_index.json', {scanned = #files, openxrMentions = hits, vrLookingPaths = vrPaths})
  return #hits, #files
end

-- --------------------------------------------------------- live capture ---

local function wrap(name, onCall)
  local xr = rawget(_G, 'OpenXR')
  if type(xr) ~= 'table' or type(xr[name]) ~= 'function' or wrapped[name] then return false end
  local original = xr[name]
  local wrapper = function(...)
    local r = pack(original(...))
    onCall(pack(...), r)
    return unpack(r, 1, r.n)
  end
  local ok = pcall(function() xr[name] = wrapper end)
  if ok and xr[name] == wrapper then wrapped[name] = original; return true end
  pcall(function() xr[name] = original end)
  return false
end

local function unwrapAll()
  local xr = rawget(_G, 'OpenXR')
  for name, original in pairs(wrapped) do
    if type(xr) == 'table' then pcall(function() xr[name] = original end) end
  end
  wrapped = {}
end

local function nums(packed)
  local out = {}
  for i = 1, packed.n do out[i] = plain(packed[i]) end
  return out
end

local phases = {
  {t = 0,  label = 'still',        msg = 'Hold BOTH controllers still in front of you and look straight ahead'},
  {t = 5,  label = 'headTurn',     msg = 'Keep controllers STILL. Slowly turn and tilt your head only'},
  {t = 10, label = 'handsMove',    msg = 'Keep head still. Move and rotate both controllers'},
  {t = 15, label = 'pointForward', msg = 'Point the RIGHT controller straight ahead like a pistol and hold'},
  {t = 19, label = 'locomotion',   msg = 'Use the stick to move and snap/smooth turn (if available)'},
}

local function phaseAt(t)
  local current = phases[1]
  for _, p in ipairs(phases) do if t >= p.t then current = p end end
  return current
end

local function sampleFrame(dtReal)
  local xr = rawget(_G, 'OpenXR')
  local s = {
    i = #capture.frames + 1,
    t = now() - capture.startTime,
    dt = dtReal,
    cam = {
      corePos = call(core_camera and core_camera.getPosition),
      coreQuat = call(core_camera and core_camera.getQuat),
      enginePos = call(rawget(_G, 'getCameraPosition')),
      engineQuat = call(rawget(_G, 'getCameraQuat')),
      name = call(core_camera and core_camera.getActiveCamName),
    },
    setterCalls = setterCalls,
    getterCalls = getterCalls,
    lastSetterArgs = lastSetter,
    lastGetterResult = lastGetter,
  }
  s.phase = phaseAt(s.t).label

  if type(xr) == 'table' then
    s.enabled = call(xr.getEnable)
    -- Native per-source poses, if this build provides them.
    if type(xr.getInputSourceStates) == 'function' then
      local okSrc, sources = pcall(xr.getInputSourceStates)
      s.inputSources = okSrc and plain(sources) or ('error: ' .. tostring(sources))
      if okSrc and type(sources) == 'table' then
        s.sourcePoses = {}
        for key, src in pairs(sources) do
          local path = type(src) == 'table' and src.path or (type(src) == 'string' and src) or nil
          if path and type(xr.getSourcePoseStates) == 'function' then
            s.sourcePoses[tostring(path)] = call(xr.getSourcePoseStates, path)
          end
        end
      end
    end
    if type(xr.getSourcePoseStates) == 'function' and not s.sourcePoses then
      s.sourcePoses = {
        ['/user/hand/left'] = call(xr.getSourcePoseStates, '/user/hand/left'),
        ['/user/hand/right'] = call(xr.getSourcePoseStates, '/user/hand/right'),
      }
    end
    -- Any other per-frame pose looking getter that takes no arguments.
    s.extra = {}
    for _, name in ipairs(capture.poseGetters) do s.extra[name] = call(xr[name]) end
  end

  capture.frames[#capture.frames + 1] = s
end

local function finishCapture(reason)
  if not capture then return end
  unwrapAll()
  local lines = {}
  for i, f in ipairs(capture.frames) do
    local ok, text = pcall(jsonEncode, f)
    lines[i] = ok and text or ('{"encodeError":"' .. tostring(text) .. '"}')
  end
  write('frames.jsonl', table.concat(lines, '\n'))
  writeJson('capture_summary.json', {
    reason = reason, frames = #capture.frames, setterCalls = setterCalls, getterCalls = getterCalls,
    wrapped = capture.wrappedNames, poseGetters = capture.poseGetters, phases = phases,
  })
  toast('Capture finished (' .. #capture.frames .. ' frames). Files in ' .. capture.userPath, 15)
  capture = nil
end

-- ---------------------------------------------------------------- public ---

function M.start(countdown, duration)
  if capture then return toast('Already capturing') end
  countdown, duration = countdown or 10, duration or 25

  outDir = '/vrPoseDump/' .. os.date('%Y%m%d_%H%M%S') .. '/'
  pcall(function() FS:directoryCreate(outDir, true) end)
  local userPath = outDir
  pcall(function() userPath = FS:getUserPath() .. outDir:sub(2) end)

  toast('Probing API and copying sources (the game may pause for a few seconds)...')
  local okA, errA = pcall(probeApi);     if not okA then log('probeApi failed: ' .. tostring(errA)) end
  local okG, errG = pcall(probeGetters); if not okG then log('probeGetters failed: ' .. tostring(errG)) end
  local okS, hits, scanned = pcall(copySources)
  if okS then log('copied ' .. tostring(hits) .. ' of ' .. tostring(scanned) .. ' Lua files that mention OpenXR')
  else log('copySources failed: ' .. tostring(hits)) end

  setterCalls, getterCalls, lastSetter, lastGetter = 0, 0, nil, nil
  local wrappedNames = {}
  if wrap('setGeluaCameraPosRot', function(args) setterCalls = setterCalls + 1; lastSetter = nums(args) end) then
    wrappedNames[#wrappedNames + 1] = 'setGeluaCameraPosRot'
  end
  if wrap('getCameraPosRotPredictedXYZXYZW', function(_, r) getterCalls = getterCalls + 1; lastGetter = nums(r) end) then
    wrappedNames[#wrappedNames + 1] = 'getCameraPosRotPredictedXYZXYZW'
  end

  -- Zero-arg getters that sound per-frame (pose, controller, hand, camera, view).
  local poseGetters = {}
  local xr = rawget(_G, 'OpenXR')
  if type(xr) == 'table' then
    local names = {}
    for k, v in pairs(xr) do if type(v) == 'function' then names[#names + 1] = tostring(k) end end
    local mt = getmetatable(xr)
    if type(mt) == 'table' and type(mt.__index) == 'table' then
      for k, v in pairs(mt.__index) do if type(v) == 'function' then names[#names + 1] = tostring(k) end end
    end
    table.sort(names)
    local seenName = {}
    for _, k in ipairs(names) do
      local lk = k:lower()
      if not seenName[k] and k:match('^get') and not skip[k] and k ~= 'getInputSourceStates'
        and k ~= 'getCameraPosRotPredictedXYZXYZW'
        and (lk:find('pose') or lk:find('controller') or lk:find('hand') or lk:find('view')
          or lk:find('camera') or lk:find('aim') or lk:find('grip') or lk:find('space')) then
        poseGetters[#poseGetters + 1] = k
      end
      seenName[k] = true
    end
  end

  capture = {
    frames = {}, countdown = countdown, duration = duration, armedAt = now(),
    startTime = nil, lastPhase = nil, wrappedNames = wrappedNames, poseGetters = poseGetters, userPath = userPath,
  }
  toast(('Put the headset on. Capture starts in %d s and lasts %d s.'):format(countdown, duration), countdown)
end

function M.stop()
  if capture then finishCapture('stopped early') end
end

function M.onPreRender(dtReal)
  if not capture then return end
  local t = now()
  if not capture.startTime then
    if t - capture.armedAt < capture.countdown then return end
    capture.startTime = t
  end
  local elapsed = t - capture.startTime
  if elapsed > capture.duration then return finishCapture('completed') end
  local phase = phaseAt(elapsed)
  if phase ~= capture.lastPhase then
    capture.lastPhase = phase
    toast(phase.msg, 5)
  end
  local ok, err = pcall(sampleFrame, dtReal)
  if not ok then capture.frames[#capture.frames + 1] = {sampleError = tostring(err)} end
end

function M.onExtensionUnloaded()
  if capture then finishCapture('extension unloaded') end
  unwrapAll()
end

return M
