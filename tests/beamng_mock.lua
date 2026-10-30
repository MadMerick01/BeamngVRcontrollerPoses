-- Minimal stand-ins for the BeamNG GE Lua globals the mod uses.
local V = {}
V.__index = V
function vec3(x, y, z)
  if type(x) == 'table' then x, y, z = x.x, x.y, x.z end
  return setmetatable({x = x or 0, y = y or 0, z = z or 0}, V)
end
V.__add = function(a, b) return vec3(a.x + b.x, a.y + b.y, a.z + b.z) end
V.__sub = function(a, b) return vec3(a.x - b.x, a.y - b.y, a.z - b.z) end
V.__mul = function(a, b)
  if type(a) == 'number' then a, b = b, a end
  return vec3(a.x * b, a.y * b, a.z * b)
end
function V:cross(b) return vec3(self.y * b.z - self.z * b.y, self.z * b.x - self.x * b.z, self.x * b.y - self.y * b.x) end
function V:dot(b) return self.x * b.x + self.y * b.y + self.z * b.z end
function V:squaredLength() return self:dot(self) end
function V:length() return math.sqrt(self:dot(self)) end
function V:normalized() local l = self:length() return vec3(self.x / l, self.y / l, self.z / l) end
function quatFromDir(dir, up) return {dir = dir, up = up} end

function ColorF(r, g, b, a) return {r, g, b, a} end
debugDrawer = {drawLine = function() end, drawSphere = function() end}
logs = {}
function log(level, tag, msg) logs[#logs + 1] = level .. ' ' .. tag .. ' ' .. msg end

mock = {time = 100, inbox = {}, camPos = vec3(), camQuat = {x = 0, y = 0, z = 0, w = 1}, camForward = vec3(0, 1, 0)}
function getCameraPosition() return mock.camPos end
function getCameraQuat() return mock.camQuat end
core_camera = {getForward = function() return mock.camForward end}
function jsonDecode(s) return mock.decode(s) end

package.preload.socket = function()
  return {
    gettime = function() return mock.time end,
    udp = function()
      return {
        settimeout = function() end,
        setsockname = function() return 1 end,
        close = function() end,
        sendto = function(_, data, ip, port)
          mock.sent = mock.sent or {}
          mock.sent[#mock.sent + 1] = {data = data, ip = ip, port = port}
          return #data
        end,
        receive = function()
          local msg = table.remove(mock.inbox, 1)
          if msg then return msg end
          return nil, 'timeout'
        end,
      }
    end,
  }
end

-- Vehicles and world for vrPistol.
FS = {fileExists = function() return false end}
mock.vehicles = {}
mock.commands = {}
mock.wallDistance = 1000
function castRayStatic(origin, dir, dist) return math.min(mock.wallDistance, dist) end
local function box(center, half)
  return {
    getCenter = function() return center end,
    getHalfExtents = function() return half end,
    getAxis = function(_, i) return ({vec3(1, 0, 0), vec3(0, 1, 0), vec3(0, 0, 1)})[i + 1] end,
  }
end
function mock.addVehicle(name, center, half)
  local veh = {name = name}
  function veh:getSpawnWorldOOBB() return box(center, half) end
  function veh:queueLuaCommand(cmd) mock.commands[#mock.commands + 1] = {name = name, cmd = cmd} end
  mock.vehicles[#mock.vehicles + 1] = veh
  return veh
end
be = {
  getPlayerVehicle = function() return mock.player end,
  getObjectCount = function() return #mock.vehicles end,
  getObject = function(_, i) return mock.vehicles[i + 1] end,
}

-- Scene objects for the pistol model: each records its last pose.
mock.objects = {}
scenetree = {findObject = function(name) return mock.objects[name] end}
function createObject(class)
  local o = {class = class, fields = {}, hidden = false}
  function o:setField(k, _, v) self.fields[k] = v end
  function o:registerObject(name) self.name = name mock.objects[name] = self end
  function o:setHidden(h) self.hidden = h end
  function o:setPosRot(x, y, z) self.pos = vec3(x, y, z) end
  function o:delete() mock.objects[self.name] = nil self.deleted = true end
  return o
end
