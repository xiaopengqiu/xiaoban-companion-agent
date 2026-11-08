// Operator-only helper: keep account and bootstrap credentials off command lines/files/output.
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
let input='';
if(process.stdin.isTTY)process.stdin.setRawMode(true);
process.stdin.setEncoding('utf8');
process.stdout.write('Ready for private account JSON on stdin (input is hidden).\n');
const data=await new Promise((resolve,reject)=>{
 process.stdin.on('data',chunk=>{
  input+=chunk;if(input.length>20000){reject(Error('Input too large'));return;}
  if(input.includes('\n')||input.includes('\r'))resolve(input.split(/[\r\n]/)[0]);
 });
 process.stdin.on('end',()=>resolve(input));
});
if(process.stdin.isTTY)process.stdin.setRawMode(false);
process.stdin.pause();
try{
 JSON.parse(data);
 const child=spawn(process.env.COMPANION_POWERSHELL_BIN||'pwsh',['-NoProfile','-NonInteractive','-File',fileURLToPath(new URL('./private-account-setup.ps1',import.meta.url))],{stdio:['pipe','pipe','pipe'],windowsHide:true});
 let output='';child.stdout.on('data',b=>{output+=b.toString('utf8');});child.stderr.resume();child.stdin.end(data+'\n');
 const code=await new Promise(resolve=>{child.on('error',()=>resolve(1));child.on('exit',resolve);});
 if(code!==0)throw Error();
 const result=JSON.parse(output.trim());
 console.log(JSON.stringify({registered:!!result.registered,administrator:!!result.administrator,email:result.email}));
}catch{console.error('Account setup failed; credentials were not printed.');process.exitCode=1;}
