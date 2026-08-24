// Loads the engine out of the built app and answers questions from Python,
// so the browser implementation can be checked against the reference one.
const fs = require("fs");

const html = fs.readFileSync(process.argv[2], "utf8");
const script = html.slice(html.indexOf("const DATA = "), html.indexOf("/* ---------------- wiring"));

globalThis.localStorage = { getItem: () => null, setItem: () => {} };
const engine = new Function(script + `
  return { P, DATA, L, bestLineup, objective, wouldStart, personalRate, maxBid, priceOf,
           dollarsPerPoint, inflation, recordSale: (id,pr,t)=>{ sales.push({id,price:pr,team:t}); },
           setDPP: () => { DPP = dollarsPerPoint(); },
           state: () => ({ myBudget: myBudget(), myOpen: myOpen(), hardCap: hardCap(),
                           dpp: DPP, inflation: inflation() }) };
`)();

const req = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
const out = {};
for (const sale of req.sales || []) engine.recordSale(sale.id, sale.price, sale.team);
engine.setDPP();

out.state = engine.state();
out.prices = (req.prices || []).map(id => engine.priceOf(engine.P.get(id)));
if (req.lineup) {
  const roster = req.lineup.map(id => engine.P.get(id));
  const r = engine.bestLineup(roster);
  out.lineupPts = r.pts;
  out.starters = r.slots.filter(s => s.p).map(s => s.p.id);
}
out.maxBids = (req.maxBids || []).map(id => engine.maxBid(engine.P.get(id)));
console.log(JSON.stringify(out));
