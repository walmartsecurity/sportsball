// Loads the engine out of the built app and answers questions from Python,
// so the browser implementation can be checked against the reference one.
const fs = require("fs");

const html = fs.readFileSync(process.argv[2], "utf8");
const script = html.slice(html.indexOf("const DATA = "), html.indexOf("/* ---------------- wiring"));

globalThis.localStorage = { getItem: () => null, setItem: () => {} };
// The mutating paths (recordSale, unsell) end in render(), which touches the
// DOM. Stub a permissive element so those paths run to completion here and the
// harness can check the state they leave behind.
const stub = new Proxy(function () {}, {
  get: (t, k) => (k === "then" ? undefined : stub),
  set: () => true, apply: () => stub, has: () => true,
});
globalThis.document = { getElementById: () => stub, querySelectorAll: () => [] };
const engine = new Function(script + `
  return { P, DATA, L, TEAMS: teamList, bestLineup, objective, wouldStart, personalRate, maxBid,
           priceOf, bidOf, bidSource, isUnderBidding, effectivePrice, pctVsValue, budgetOf, playersOf,
           openSlotsOf, maxBidOf, leagueLeft, slotsLeft, targetRoster,
           setExpected: (id, v) => { if (v === null) delete expected[id]; else expected[id] = v; },
           setTeamEdit: (t, f, v) => { (teamEdits[t] = teamEdits[t] || {})[f] = v; },
           applyToEdit, load, seedState, SEED,
           setRoomBid: (id, v) => { roomBids[id] = v; }, suggestions, startableLeft, qbJobsLeft, roomPricing,
           dollarsPerPoint, inflation, recordSale: (id,pr,t)=>{ sales.push({id,price:pr,team:t}); },
           setDPP: () => { DPP = dollarsPerPoint(); },
           nowPrice, nowIsLive, unsell, sales: () => sales, roomBids: () => roomBids,
           state: () => ({ myBudget: myBudget(), myOpen: myOpen(), hardCap: hardCap(),
                           dpp: DPP, inflation: inflation() }) };
`)();

const req = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
const out = {};
// The app calls load() on start; the harness slices that off, so do it here
// when a test wants the state the board actually ships with.
if (req.load) engine.load();
for (const [team, edit] of Object.entries(req.teamEdits || {})) {
  for (const [field, value] of Object.entries(edit)) engine.setTeamEdit(team, field, value);
}
for (const [id, price] of Object.entries(req.expected || {})) engine.setExpected(id, price);
for (const [id, price] of Object.entries(req.roomBids || {})) engine.setRoomBid(id, price);
for (const sale of req.sales || []) {
  engine.recordSale(sale.id, sale.price, sale.team);
  engine.applyToEdit(sale.team, sale.price, 1);
}
for (const u of req.unsell || []) engine.unsell(u.id, !!u.standingBid);
engine.setDPP();

out.state = engine.state();
out.room = engine.roomPricing();
out.league = { money: engine.leagueLeft(), slots: engine.slotsLeft() };
out.teams = Object.fromEntries(engine.TEAMS().map(t => [t, {
  budget: engine.budgetOf(t), players: engine.playersOf(t),
  open: engine.openSlotsOf(t), max: engine.maxBidOf(t),
}]));
out.bids = (req.bids || []).map(id => {
  const p = engine.P.get(id);
  return { id, bid: engine.bidOf(p), effective: engine.effectivePrice(p),
           pct: engine.pctVsValue(p), value: p.val,
           source: engine.bidSource(p), open: engine.isUnderBidding(p),
           now: engine.nowPrice(p), nowLive: engine.nowIsLive(p) };
});
if (req.plan) {
  const plan = engine.targetRoster();
  out.plan = {
    additions: plan.additions.map(p => p.id),
    spend: plan.spend,
    lineupPts: plan.lineup.pts,
    starters: plan.lineup.slots.filter(s => s.p).map(s => s.p.id),
  };
}
out.prices = (req.prices || []).map(id => engine.priceOf(engine.P.get(id)));
if (req.lineup) {
  const roster = req.lineup.map(id => engine.P.get(id));
  const r = engine.bestLineup(roster);
  out.lineupPts = r.pts;
  out.starters = r.slots.filter(s => s.p).map(s => s.p.id);
}
out.maxBids = (req.maxBids || []).map(id => engine.maxBid(engine.P.get(id)));
if (req.suggest) {
  out.suggestions = engine.suggestions(req.suggest).map(r => ({
    id: r.p.id, pos: r.p.pos, price: r.price, max: r.mb, edge: r.edge,
    reasons: r.reasons, urgent: r.urgent,
  }));
}
console.log(JSON.stringify(out));
