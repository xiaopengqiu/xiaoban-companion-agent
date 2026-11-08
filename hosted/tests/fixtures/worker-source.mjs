import {build} from 'esbuild';
import {fileURLToPath} from 'node:url';

let bundled;
// Match production's browser bundle while preserving the empty test asset marker.
export async function workerSource(){
 bundled??=build({entryPoints:[fileURLToPath(new URL('../../worker/index.js',import.meta.url))],bundle:true,format:'esm',platform:'browser',target:'es2022',write:false}).then(result=>result.outputFiles[0].text);
 return bundled;
}
