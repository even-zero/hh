import test from 'node:test';
const DUR = {"js_t000": 0.0087, "js_t001": 0.0091, "js_t002": 0.0268, "js_t003": 0.0362, "js_t004": 0.0353, "js_t005": 0.0207, "js_t006": 0.0083, "js_t007": 0.0245, "js_t008": 0.0103, "js_t009": 0.0193, "js_t010": 0.0167, "js_t011": 0.0212, "js_t012": 0.0159, "js_t013": 0.0106, "js_t014": 0.0111, "js_t015": 0.0282, "js_t016": 0.0115, "js_t017": 0.0157, "js_t018": 0.0044, "js_t019": 0.0114};
const sleep = (ms) => new Promise(r => setTimeout(r, ms));
for (const [name, ms] of Object.entries(DUR)) {
  test(name, async () => { await sleep(ms * 1000); });
}
