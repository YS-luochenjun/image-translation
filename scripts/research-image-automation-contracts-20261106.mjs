import {connectProxy} from './fireplay-mcp-publication.mjs';
import {discoverTranslations} from './fireplay-discover-translations.mjs';
const transport=await connectProxy();
try {
 const result=await discoverTranslations({transport,deptId:'od-354b90f636b89e8d390a37e2946f9e17'});
 console.log(JSON.stringify({state:result.state,complete:result.complete,pages:result.summary_pages.length,scanned:result.summary_pages.reduce((sum,page)=>sum+page.response.items.length,0),option_evidence:result.option_evidence,candidates:result.candidates,external_write:result.external_write},null,2));
} finally {transport.close();}
