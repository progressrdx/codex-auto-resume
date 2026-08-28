// Isolated simulator project. Never changes the release project's domain checks.
const fs=require('node:fs');
const os=require('node:os');
const path=require('node:path');
const root=fs.mkdtempSync(path.join(os.tmpdir(),'codex-relay-wechat-dev-'));
fs.cpSync(path.resolve(__dirname,'../miniprogram'),root,{recursive:true,filter:p=>!p.endsWith('project.private.config.json')});
const file=path.join(root,'project.config.json'),config=JSON.parse(fs.readFileSync(file,'utf8'));
config.setting.urlCheck=false;
config.projectname='relay-isolated-simulator-test';
fs.writeFileSync(file,JSON.stringify(config,null,2)+'\n');
console.log(root);
