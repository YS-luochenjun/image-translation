// Across hot-reloaded instances of this project Host bundle, all production
// admissions use one queue. Read-only calls share it to observe stable slots.
const key=Symbol.for('local.fireplay.production.admission.v1');
const state=globalThis[key]??= {tail:Promise.resolve()};
export function admit(operation){
 const result=state.tail.then(operation);
 state.tail=result.catch(()=>{});
 return result;
}
