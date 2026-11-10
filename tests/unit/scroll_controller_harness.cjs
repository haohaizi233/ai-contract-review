const assert = require('node:assert/strict');
let frames = [];
let activeAnnotationIndex = -1;
const requestAnimationFrame = fn => (frames.push(fn), frames.length);
const cancelAnimationFrame = () => { frames = []; };
const flush = () => { const f = frames; frames = []; f.forEach(fn => fn()); };
const window = { matchMedia: () => ({matches: false}), addEventListener() {} };
class Observer { constructor(callback) { this.callback = callback; } observe() {} disconnect() {} }
const ResizeObserver = Observer, MutationObserver = Observer;
function pane(id) {
  return {id, scrollTop: 0, scrollHeight: 2400, clientHeight: 400, clientTop: 0,
    listeners: {}, children: [], classList: {add(){}, remove(){}},
    addEventListener(type, fn) { this.listeners[type] = fn; },
    getBoundingClientRect() { return {top: 100, height: 400}; },
    querySelectorAll() { return this.children; },
    scrollTo({top}) { this.scrollTop = top; },
    fire(type) { this.listeners[type]?.({target: this, key: 'ArrowDown'}); }};
}
const std = pane('colStandardContainer'), rev = pane('colReviewedContainer'), detail = pane('annotationsList');
const elements = {colStandardContainer: std, colReviewedContainer: rev, annotationsList: detail};
function child(id, parent, top, height=100) {
 const classes = new Set();
 const el = {id, offsetHeight: height, classList: {add(c){classes.add(c)}, remove(c){classes.delete(c)}, contains(c){return classes.has(c)}},
 getBoundingClientRect() { return {top: 100 + top - parent.scrollTop, height}; }};
 elements[id] = el; parent.children.push(el); return el;
}
child('std-b1', std, 300); child('rev-b1', rev, 400);
child('std-b2', std, 1500); child('rev-b2', rev, 1600);
child('card-a1', detail, 200); child('card-a2', detail, 1400);
let currentData = {aligned_blocks: [{block_id:'b1', annotation_ids:['a1']},{block_id:'b2', annotation_ids:['a2']}], annotations:[{id:'a1',block_id:'b1'},{id:'a2',block_id:'b2'}]};
const document = {getElementById: id => elements[id] || null, querySelectorAll: () => [], fonts: {ready: Promise.resolve()}};
/* CONTROLLER */
assert.equal(mapScrollPosition(50, [[0,0],[100,200]]), 100);
assert.equal(mapScrollPosition(-5, [[0,0],[100,200]]), 0);
assert.equal(mapScrollPosition(500, [[0,0],[100,200]]), 200);
setupThreeWaySyncScroll();
std.fire('wheel'); std.scrollTop = 800; std.fire('scroll'); flush();
assert.ok(rev.scrollTop > 800, 'different clause geometry must be mapped');
assert.ok(elements['card-a1'].classList.contains('active'), 'scroll must highlight the corresponding annotation');
assert.equal(activeAnnotationIndex, 0, 'next/previous navigation must track the visible annotation');
const previous = std.scrollTop;
rev.fire('scroll'); detail.fire('scroll'); flush();
assert.equal(std.scrollTop, previous, 'follower events must not feed back');
detail.fire('wheel'); detail.scrollTop += 20; detail.fire('scroll'); flush();
assert.notEqual(std.scrollTop, previous, 'new gesture must immediately take ownership');
const before = std.scrollTop;
detail.scrollTop += 1; detail.fire('scroll'); flush();
assert.ok(Math.abs(std.scrollTop-before) < 10, 'right pane must interpolate rather than jump clauses');
std.fire('pointerdown'); std.scrollTop = 2000; std.fire('scroll'); flush();
assert.equal(rev.scrollTop, 2000); assert.equal(detail.scrollTop, 2000);
console.log('scroll controller regression checks passed');
// Navigation never feeds back; a physical gesture cancels it immediately.
activeDriver = 'navigation';
const navBefore = rev.scrollTop;
std.scrollTop = 500; std.fire('scroll'); flush();
assert.equal(rev.scrollTop, navBefore);
rev.fire('wheel'); rev.scrollTop = 700; rev.fire('scroll'); flush();
assert.notEqual(std.scrollTop, 500);
// Filters can remove every card; zero-height panes and stale geometry remain finite.
detail.children = []; detail.scrollHeight = detail.clientHeight;
invalidateScrollGeometry();
std.fire('wheel'); std.scrollTop = 1000; std.fire('scroll'); flush();
assert.equal(detail.scrollTop, 0);
assert.ok(Number.isFinite(rev.scrollTop));
// A resized pane rebuilds endpoint geometry on its next scroll.
rev.scrollHeight = 3400; invalidateScrollGeometry();
std.scrollTop = 2000; std.fire('scroll'); flush();
assert.equal(rev.scrollTop, 3000);

// A new render must not inherit a previous document's navigation lock.
activeDriver = 'navigation';
setupThreeWaySyncScroll();
const resetBefore = rev.scrollTop;
std.scrollTop = 900; std.fire('scroll'); flush();
assert.notEqual(rev.scrollTop, resetBefore, 'new document must clear old navigation ownership');

// Restore detail cards and height for right-pane driver tests.
detail.children = [elements['card-a1'], elements['card-a2']];
detail.scrollHeight = 2400;
rev.scrollHeight = 2400;
std.scrollHeight = 2400;
invalidateScrollGeometry();

// Right pane driving left and middle panes monotonically
detail.fire('wheel');
detail.scrollTop = 600;
detail.fire('scroll'); flush();
const stdAt600 = std.scrollTop;
const revAt600 = rev.scrollTop;
assert.ok(stdAt600 > 50 && stdAt600 < 2000, 'right pane must smoothly drive left pane: ' + stdAt600);
assert.ok(revAt600 > 50 && revAt600 < 2000, 'right pane must smoothly drive middle pane: ' + revAt600);

detail.scrollTop = 1400;
detail.fire('scroll'); flush();
assert.ok(std.scrollTop > stdAt600, 'further scrolling right pane must advance left pane monotonically');
assert.ok(rev.scrollTop > revAt600, 'further scrolling right pane must advance middle pane monotonically');
console.log('all right-pane-driven tests passed');
