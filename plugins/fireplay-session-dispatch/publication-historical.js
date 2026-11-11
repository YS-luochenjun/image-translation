// OFFLINE CANDIDATE ONLY. No I/O, execution, network, or production installation.
import {createHash} from 'node:crypto';

export const CONTRACT_SHA256 = '35ec2f79621d298bdf87cc1e04f59377d69d001f87f3c07d15f04f20ec3fbadf';
const KNOWN_STATES = new Set(['received','completed','verified','failed','reconciled','not_started']);
const WRITE_TOOLS = new Set(['material_r2_upload_from_bytes','material_upload_from_url','material_attribution_update','requirement_bind_materials']);
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const assert = (condition, message) => { if (!condition) throw Error(message); };
const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const safeName = name => typeof name === 'string' && /^[A-Za-z0-9][A-Za-z0-9._-]*\.jsonl?$/.test(name) && !name.includes('..');
const equal = (a,b) => JSON.stringify(a) === JSON.stringify(b);

function exactShape(value, expected) {
  if (expected.type === 'array') return Array.isArray(value) && value.length === expected.items.length && value.every((item,index) => exactShape(item,expected.items[index]));
  if (expected.type === 'object') return object(value) && equal(Object.keys(value).sort(),Object.keys(expected.fields)) && Object.entries(expected.fields).every(([key,shape]) => exactShape(value[key],shape));
  return (value === null ? 'null' : typeof value) === expected.type;
}

function parseContract(bytes, row) {
  assert(hash(bytes) === CONTRACT_SHA256,'Historical contract is not the compiled reviewed contract');
  const manifest = JSON.parse(bytes);
  assert(manifest.version === 'historical-artifact-evidence-v1','Unsupported historical contract');
  const contract = manifest.contracts.find(entry => entry.requirement_id === row.id && entry.code === row.code);
  assert(contract && contract.session_id === row.session_id && contract.snapshot_sha256 === row.snapshot_sha256,'Historical scope/session/snapshot mismatch');
  return contract;
}

export function historicalEvidenceNames({contractBytes,row}) {
  const contract = parseContract(contractBytes,row);
  const names = [...new Set([...contract.witnesses.map(file => file.name),...contract.artifacts.flatMap(file => file.plan_references.map(ref => ref.request))])];
  assert(names.every(safeName),'Historical evidence path outside direct requirement root');
  return names;
}

function knownEvidence(contract, evidence) {
  assert(object(evidence),'Historical evidence map required');
  const allowedNames = historicalNames(contract);
  assert(Object.keys(evidence).every(name => allowedNames.has(name) && safeName(name)),'Unexpected historical evidence path');
  const journals = new Map();
  for (const file of contract.witnesses) {
    assert(safeName(file.name) && evidence[file.name] && hash(evidence[file.name]) === file.sha256,'Historical witness missing/changed');
    if (file.kind !== 'journal') continue;
    const rows = evidence[file.name].toString('utf8').split(/\r?\n/).filter(line => line.trim()).map(line => JSON.parse(line));
    const latest = new Map();
    for (const entry of rows) {
      assert(object(entry) && typeof entry.operation_id === 'string' && entry.operation_id.length > 0,'Journal operation identity required');
      assert(!entry.requirement_id || entry.requirement_id === contract.requirement_id,'Journal requirement scope mismatch');
      assert(!entry.code || entry.code === contract.code,'Journal code scope mismatch');
      latest.set(entry.operation_id,entry);
    }
    for (const entry of latest.values()) assert(entry.inflight_unknown !== true && entry.unknown_inflight !== true && KNOWN_STATES.has(entry.state ?? entry.stage),'Latest journal outcome unknown');
    journals.set(file.name,latest);
  }
  const publication = journals.get('publication-journal.jsonl');
  assert(publication && publication.size === contract.received_outcomes.length,'Publication witness operations incomplete');
  for (const expected of contract.received_outcomes) {
    const entry = publication.get(expected.operation_id);
    assert(entry?.state === 'received' && entry.response && entry.response.isError !== true,'Received RPC response required');
    assert(entry.tool === expected.tool && entry.schema_version === expected.schema_version && entry.request_sha256 === expected.request_sha256 && hash(JSON.stringify(entry.response)) === expected.response_sha256,'RPC tool/schema/request/response witness mismatch');
    const receipt = JSON.parse(evidence[expected.name]);
    const withoutTimestamp = Object.fromEntries(Object.entries(entry).filter(([key]) => key !== 'at'));
    assert(equal(receipt,withoutTimestamp),'Independent receipt differs from latest durable outcome');
  }
  return publication;
}

function historicalNames(contract) {
  return new Set([...contract.witnesses.map(file => file.name),...contract.artifacts.flatMap(file => file.plan_references.map(ref => ref.request))]);
}

function noInlinePayload(value) {
  if (!value || typeof value !== 'object') return;
  assert(!Object.hasOwn(value,'file_base64') && !Object.hasOwn(value,'image_base64') && !Object.hasOwn(value,'authorization') && !Object.hasOwn(value,'access_token'),'Inline sensitive/executable payload is not a historical request copy');
  for (const item of Object.values(value)) noInlinePayload(item);
}

export function verifyHistoricalArtifact({name,bytes,declaration,inventory,row,contractBytes,evidence}) {
  if (declaration === undefined) return false;
  assert(object(declaration) && equal(Object.keys(declaration).sort(),['artifact_sha256','contract_sha256','evidence_note','kind']),'Invalid historical declaration fields');
  assert(declaration.contract_sha256 === CONTRACT_SHA256 && typeof declaration.evidence_note === 'string' && declaration.evidence_note.trim(),'Explicit parent historical contract evidence required');
  assert(safeName(name),'Historical artifact scope path rejected');
  const contract = parseContract(contractBytes,row);
  const expected = contract.artifacts.find(file => file.name === name);
  assert(expected && declaration.kind === expected.kind && declaration.artifact_sha256 === expected.sha256 && inventory[name] === expected.sha256 && hash(bytes) === expected.sha256,'Historical artifact is not the exact reviewed bytes');
  const value = JSON.parse(bytes);
  assert(exactShape(value,expected.exact_shape) && hash(JSON.stringify(value)) === expected.serialized_sha256,'Historical artifact fields/value changed');
  assert(value.inflight_unknown !== true && value.unknown_inflight !== true,'Unknown operation cannot be historical artifact');
  const publication = knownEvidence(contract,evidence);
  if (expected.kind === 'received_request_copy') {
    noInlinePayload(value);
    const outcome = publication.get(expected.received_operation_id);
    assert(WRITE_TOOLS.has(value.tool) && value.tool === expected.tool && value.schema_version === expected.schema_version,'Historical request tool/schema allowlist mismatch');
    assert(object(value.arguments) && outcome?.tool === value.tool && outcome.schema_version === value.schema_version && outcome.request_sha256 === hash(JSON.stringify(value)),'Unreceived actionable request rejected');
    assert(!Object.hasOwn(value,'operation_id') && !Object.hasOwn(value,'state') && !Object.hasOwn(value,'stage'),'Request copy cannot claim operation state');
  } else if (expected.kind === 'historical_plan') {
    assert(!Object.hasOwn(value,'tool') && !Object.hasOwn(value,'arguments') && expected.plan_references.length > 0,'Actionable or uncorrelated plan rejected');
    for (const ref of expected.plan_references) {
      assert(safeName(ref.request),'Plan path outside requirement');
      const requestBytes = evidence[ref.request];
      assert(requestBytes,'Plan request evidence missing');
      const request = JSON.parse(requestBytes);
      const outcome = publication.get(ref.operation_id);
      noInlinePayload(request);
      assert(outcome?.state === 'received' && hash(JSON.stringify(request)) === ref.request_sha256 && outcome.request_sha256 === ref.request_sha256 && outcome.tool === request.tool && outcome.schema_version === request.schema_version,'Plan operation/request correlation mismatch');
    }
  } else {
    assert(['catalog_snapshot','historical_summary'].includes(expected.kind),'Unsupported historical artifact kind');
    for (const record of Array.isArray(value) ? value : [value]) assert(object(record) && !['operation_id','arguments','tool','request_sha256','remote_write_attempted','inflight_unknown','unknown_inflight'].some(key => Object.hasOwn(record,key)),'Operation cannot masquerade as metadata');
  }
  // This acknowledges evidence only. It cannot authorize or perform any write.
  return true;
}
