const test = require('node:test');
const assert = require('node:assert/strict');
require('./status.js');
test('only unambiguous, standalone state labels are accepted', () => {
  assert.equal(GGABStatus.parse('Random match 1\nFinalized'), 'finalized');
  assert.equal(GGABStatus.parse('IN QUEUE\nYour match is in the queue'), 'processing');
  assert.equal(GGABStatus.parse('Finalized\nIN QUEUE'), null);
  assert.equal(GGABStatus.parse('A player named Finalized'), null);
  assert.equal(GGABStatus.parse('Loading…'), null);
  assert.equal(GGABStatus.parse('VIDEO UNAVAILABLE'), 'failed');
});
test('only exact GGAB project URLs supply an association ID', () => {
  const id = '55f9891e-86b0-41a3-bd1c-e6b4ee629606';
  assert.equal(GGABStatus.id('https://app.getgoodatbadminton.com/tracker/' + id + '?autotrack=1'), id);
  assert.equal(GGABStatus.id('https://example.com/tracker/' + id), null);
  assert.equal(GGABStatus.id('https://app.getgoodatbadminton.com/tracker/new'), undefined);
});
