import {readFile,writeFile,mkdir,readdir,cp} from 'node:fs/promises';
import {existsSync} from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {build} from 'esbuild';

const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const candidates=[process.env.WEB_SOURCE_DIR,path.resolve(root,'../web'),path.resolve(root,'web')].filter(Boolean);
const web=candidates.find(dir=>existsSync(path.join(dir,'index.html')));
if(!web)throw new Error('Missing web source. Set WEB_SOURCE_DIR or provide ../web or ./web.');
const assets={};
for(const file of await readdir(web,{withFileTypes:true})){
  if(!file.isFile()||! /\.(html|css|js|mjs|svg)$/.test(file.name))continue;
  let text=await readFile(path.join(web,file.name),'utf8');
  if(file.name==='index.html'&&!text.includes('name="companion-runtime"'))text=text.replace(/<head>/i,'<head>\n<meta name="companion-runtime" content="cloud">');
  assets['/'+file.name]=text;
}
const manifest=JSON.parse(await readFile(path.join(root,'.openai/hosting.json'),'utf8'));
if(!manifest.project_id||manifest.static||manifest.d1!=='DB')throw new Error('A registered Worker Site with d1: "DB" is required.');
const source=await readFile(path.join(root,'worker/index.js'),'utf8');
if(!source.includes('/*__ASSETS__*/ {}'))throw new Error('Missing static asset build marker');
await mkdir(path.join(root,'dist/server'),{recursive:true});
await mkdir(path.join(root,'dist/.openai'),{recursive:true});
await build({stdin:{contents:source.replace('/*__ASSETS__*/ {}',JSON.stringify(assets)),resolveDir:path.join(root,'worker'),sourcefile:'index.js'},bundle:true,format:'esm',platform:'browser',target:'es2022',outfile:path.join(root,'dist/server/index.js')});
await writeFile(path.join(root,'dist/.openai/hosting.json'),JSON.stringify(manifest,null,2)+'\n');
await cp(path.join(root,'drizzle'),path.join(root,'dist/.openai/drizzle'),{recursive:true});
console.log('Built self-contained Worker, '+Object.keys(assets).length+' static assets, and schema migrations.');
