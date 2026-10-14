import assert from 'node:assert/strict'
import { test } from 'node:test'

import { compareVersions, isPrivateAddress, newestSupportedVersion, offlineUuid } from '../src/config.js'
import { describeState } from '../src/describe.js'
import { bearingWord, compass, dayPhase, relativeBearing } from '../src/state.js'

test('bearing is relative to her facing, positive to the right', () => {
  // Facing north (yaw 0): east (+x) is on her right, west on her left, south behind.
  assert.equal(Math.round(relativeBearing(0, 1, 0)), 90)
  assert.equal(Math.round(relativeBearing(0, -1, 0)), -90)
  assert.equal(Math.abs(Math.round(relativeBearing(0, 0, 1))), 180)
  assert.equal(Math.round(relativeBearing(0, 0, -1)), 0)
  // Facing west (yaw pi/2): north (-z) is on her right.
  assert.equal(Math.round(relativeBearing(Math.PI / 2, 0, -1)), 90)
})

test('bearing words cover every side', () => {
  assert.equal(bearingWord(0), 'ahead')
  assert.equal(bearingWord(45), 'ahead-right')
  assert.equal(bearingWord(-90), 'left')
  assert.equal(bearingWord(-140), 'behind-left')
  assert.equal(bearingWord(180), 'behind')
  assert.equal(bearingWord(-170), 'behind')
})

test('compass follows mineflayer yaw', () => {
  assert.equal(compass(0), 'north')
  assert.equal(compass(Math.PI / 2), 'west')
  assert.equal(compass(Math.PI), 'south')
  assert.equal(compass(-Math.PI / 2), 'east')
})

test('day phases', () => {
  assert.equal(dayPhase(1000), 'day')
  assert.equal(dayPhase(12500), 'sunset')
  assert.equal(dayPhase(18000), 'night')
  assert.equal(dayPhase(23500), 'sunrise')
})

test('only LAN and loopback addresses are private', () => {
  for (const ok of ['127.0.0.1', '192.168.1.76', '10.0.0.5', '172.20.1.1', '::1', '::ffff:192.168.1.2', 'fd00::1']) {
    assert.ok(isPrivateAddress(ok), ok)
  }
  for (const bad of ['8.8.8.8', '172.32.0.1', '2001:4860::8888', 'example.com']) {
    assert.ok(!isPrivateAddress(bad), bad)
  }
})

test('offline UUID matches what a vanilla offline server assigns', () => {
  // Known value: Java's UUID.nameUUIDFromBytes("OfflinePlayer:Notch").
  assert.equal(offlineUuid('Notch'), 'b50ad385-829d-3141-a216-7e7d7539ba7f')
})

test('describeState renders the few lines her hands will read', () => {
  const text = describeState({
    username: 'Columbina',
    dimension: 'minecraft:overworld',
    position: { x: 1.5, y: 64, z: -3.2 },
    facing: { compass: 'north', pitch_down_deg: 25 },
    standing_on: 'grass_block',
    in_water: false,
    health: 18,
    food: 20,
    held: { name: 'wooden_pickaxe', count: 1 },
    inventory: [{ name: 'oak_log', count: 5 }],
    time: { day: 2, phase: 'night', time_of_day: 15000 },
    weather: 'clear',
    nearby: [{ label: 'zombie', distance: 6.2, side: 'behind-left', dy: -3 }],
    blocks: [{ kind: 'log', name: 'oak_log', distance: 4, side: 'ahead', dy: 0 }]
  })
  assert.match(text, /facing north, looking 25° down, standing on grass_block/)
  assert.match(text, /Holding wooden_pickaxe x1/)
  assert.match(text, /Day 2, night/)
  assert.match(text, /zombie 6.2 m behind-left, 3 m below/)
  assert.match(text, /Blocks: oak_log 4 m ahead\./)
})

test('versions compare by release number across the 1.x -> 26.x change', () => {
  assert.equal(compareVersions('1.21.4', '26.1'), -1)
  assert.equal(compareVersions('26.1.2', '26.1'), 1)
  assert.equal(compareVersions('1.21.11', '1.21.4'), 1)
  assert.equal(compareVersions('26.1', '26.1.0'), 0)
})

test('default version is the newest patch release with Mineflayer\'s protocol', () => {
  // Mineflayer 4.39 supports 26.1; 26.1.1 and 26.1.2 share its protocol.
  assert.equal(newestSupportedVersion(), '26.1.2')
})
