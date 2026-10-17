const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const source = fs.readFileSync(__dirname + '/../static/tracker.js', 'utf8');
function part(start, end) {
  const from = source.indexOf(start), to = source.indexOf(end, from);
  assert.ok(from >= 0 && to > from, 'shared frame function boundaries found');
  return source.slice(from, to);
}
class SvgNode {
  constructor(tag) {this.tag = tag; this.children = []; this.attributes = {}; this.dataset = {};
    this.classList = {add: (...names) => {this.classes = [...(this.classes || []), ...names];}};}
  append(...nodes) {this.children.push(...nodes);}
  replaceChildren(...nodes) {this.children = nodes;}
  setAttribute(key, value) {this.attributes[key] = String(value);}
}
function makeModel() {
  const boxes = new SvgNode('g');
  const context = {document: {createElementNS: (_, name) => new SvgNode(name)},
    el: id => {assert.equal(id, 'player-boxes'); return boxes;},
    persist: () => {}, renderPlayer: () => {}, confirm: () => true,
    invalidate: p => {p.revision++; p.snapshot = null; p.box = null; p.saving = false;}};
  vm.createContext(context);
  vm.runInContext('let state=fresh(); let slot="A1"; ' +
    part('function fresh()', 'function selectedVideo()') +
    part('function drawBoxes(', 'function renderSnapshot()') +
    part('function replaceSharedFrame(', 'function changeTime('), context);
  return {context, boxes};
}

test('two player boxes remain on the same frame with separate identities', () => {
  const {context, boxes} = makeModel();
  vm.runInContext('player("A1").box={x:.1,y:.2,width:.2,height:.3}; player("B1").box={x:.6,y:.3,width:.2,height:.3}; drawBoxes();', context);
  assert.deepEqual(boxes.children.map(g => g.dataset.slot), ['A1', 'B1']);
  assert.equal(boxes.children[0].children[0].attributes.x, '100');
  assert.equal(boxes.children[1].children[0].attributes.x, '600');
  vm.runInContext('slot="B1"; drawBoxes();', context);
  assert.deepEqual(boxes.children.map(g => g.dataset.slot), ['A1', 'B1']);
  assert.ok(boxes.children[1].classes.includes('is-active'));
});

test('changing shared frame clears both snapshots and updates both frame coordinates', () => {
  const {context, boxes} = makeModel();
  vm.runInContext('for(const s of slots()){const p=player(s);p.box={x:.1,y:.2,width:.2,height:.3};p.snapshot={id:s};} replaceSharedFrame(.5,90);', context);
  const actual = vm.runInContext('slots().map(s=>({time:player(s).time,rotation:player(s).rotation,box:player(s).box,snapshot:player(s).snapshot}))', context);
  assert.equal(JSON.stringify(actual), JSON.stringify(Array(2).fill({time:.5,rotation:90,box:null,snapshot:null})));
  assert.equal(boxes.children.length, 0);
});

test('cancelling a frame change preserves all saved boxes', () => {
  const {context} = makeModel();
  context.confirm = () => false;
  vm.runInContext('for(const s of slots())player(s).box={x:.1,y:.2,width:.2,height:.3}; replaceSharedFrame(.5,90);', context);
  assert.equal(vm.runInContext('sharedFrame().time', context), 0);
  assert.equal(vm.runInContext('slots().filter(s=>!!player(s).box).length', context), 2);
});
