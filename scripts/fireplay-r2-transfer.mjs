const HTTPS = 'https:';

function uploadTarget(row, expected, uniqueResources) {
  if (row?.success === false || row?.file_name !== expected.file_name ||
      row.file_size !== expected.file_size || !uniqueResources.has(row.resourceId)) return null;
  let address, publicAddress;
  try {
    address = new URL(row.putUrl);
    publicAddress = new URL(row.publicUrl);
  } catch {
    return null;
  }
  if (address.protocol !== HTTPS || address.username || address.password || address.port || address.hash ||
      !address.hostname.endsWith('.r2.cloudflarestorage.com') ||
      publicAddress.protocol !== HTTPS || publicAddress.username || publicAddress.password ||
      publicAddress.search || publicAddress.hash || address.href === publicAddress.href) return null;
  const headers = new Headers();
  try {
    for (const [name, value] of Object.entries(row.upload_headers ?? {})) headers.append(name, value);
  } catch {
    return null;
  }
  if ([...headers.keys()].some(name => !['content-type', 'content-length'].includes(name)) ||
      (headers.get('content-type') ?? row.content_type) !== expected.content_type ||
      (headers.has('content-length') && headers.get('content-length') !== String(expected.file_size))) return null;
  headers.set('content-type', expected.content_type);
  return { address, headers, resourceId: row.resourceId, publicUrl: row.publicUrl };
}

export function matchUploadTargets(result, items, isResourceId) {
  if (result?.success === false || result?.error || !Array.isArray(result?.items)) return items.map(() => null);
  const resourceCounts = new Map(), byName = new Map();
  for (const row of result.items) {
    if (!row || typeof row !== 'object') continue;
    if (isResourceId.test(row.resourceId)) {
      const id = row.resourceId.toLowerCase();
      resourceCounts.set(id, (resourceCounts.get(id) ?? 0) + 1);
    }
    byName.set(row.file_name, byName.has(row.file_name) ? null : row);
  }
  const uniqueResources = new Set(result.items.flatMap(row =>
    typeof row?.resourceId === 'string' && resourceCounts.get(row.resourceId.toLowerCase()) === 1 ? [row.resourceId] : []));
  return items.map(item => uploadTarget(byName.get(item.file_name), item, uniqueResources));
}

export async function putImage(target, bytes) {
  const response = await fetch(target.address, {
    method: 'PUT',
    headers: target.headers,
    body: bytes,
    redirect: 'error',
    signal: AbortSignal.timeout(120_000),
  });
  await response.body?.cancel().catch(() => {});
  return response.status;
}
