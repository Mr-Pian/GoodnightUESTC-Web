'use strict';
// School-page frames and input remain scoped to the authenticated workspace.
const loginDialog = document.createElement('dialog');
loginDialog.className = 'remote-login';
loginDialog.setAttribute('aria-label', '学校登录页面');
loginDialog.innerHTML = `<h2>完成学校登录</h2>
<p>这是你的独立学校登录页面。可点击、拖动验证控件；输入验证码前先点击页面中的对应输入框。完成后页面会自动关闭。</p>
<p id="remote-login-state" aria-live="polite">正在加载学校页面…</p>
<img id="remote-login-image" alt="当前学校登录页面" draggable="false" hidden>
<form id="remote-text-form"><label for="remote-text">向已选中的学校输入框输入内容</label>
<input id="remote-text" type="password" maxlength="256" autocomplete="off" placeholder="验证码或验证信息">
<button class="button secondary" type="submit">发送输入</button></form>
<div class="remote-controls"><button type="button" data-key="Tab">下一个输入框</button><button type="button" data-key="Control+A">全选</button><button type="button" data-key="Backspace">删除</button><button type="button" data-key="Enter">回车</button><button type="button" data-scroll="-500">向上滚动</button><button type="button" data-scroll="500">向下滚动</button><button type="button" id="remote-cancel">取消登录</button></div>`;
document.body.append(loginDialog);
const remoteImage = $('remote-login-image');
let remoteActive = false, frameBusy = false, pointerStart;
async function remoteInput(action) {
  try { await api('/api/login/input', action); }
  catch (e) { toast(e.message); }
}
function imagePoint(event) {
  const bounds = remoteImage.getBoundingClientRect();
  return {x: Math.max(0, Math.min(999, (event.clientX-bounds.left)*1000/bounds.width)),
          y: Math.max(0, Math.min(759, (event.clientY-bounds.top)*760/bounds.height))};
}
remoteImage.addEventListener('pointerdown', e => {e.preventDefault();pointerStart=imagePoint(e);remoteImage.setPointerCapture(e.pointerId);});
remoteImage.addEventListener('pointercancel', () => {pointerStart=null;});
remoteImage.addEventListener('pointerup', e => {
  if (!pointerStart) return;
  const end=imagePoint(e), start=pointerStart;pointerStart=null;
  remoteInput(Math.hypot(end.x-start.x,end.y-start.y)>5?
    {kind:'drag',...start,to_x:end.x,to_y:end.y}:{kind:'click',...end});
});
$('remote-text-form').addEventListener('submit', e => {
  e.preventDefault();const text=$('remote-text').value;$('remote-text').value='';
  if(text)remoteInput({kind:'text',text});
});
loginDialog.querySelectorAll('[data-key]').forEach(button => button.addEventListener('click',()=>remoteInput({kind:'key',key:button.dataset.key})));
loginDialog.querySelectorAll('[data-scroll]').forEach(button => button.addEventListener('click',()=>remoteInput({kind:'scroll',delta:Number(button.dataset.scroll)})));
$('remote-cancel').addEventListener('click',()=>{$('cancel-login').click();});
loginDialog.addEventListener('cancel',e=>{e.preventDefault();$('cancel-login').click();});
document.addEventListener('workspace-status', event => {
  const state=event.detail;
  if(!state.hosted)return;
  if(!loginDialog.dataset.ready){
    loginDialog.dataset.ready='true';document.title='晚安成电 · 测试组工作台';
    document.querySelector('.brand small').textContent='TEAM WORKSPACE';
    document.querySelector('.mode').textContent='手动签到 · 测试组专用';
    document.querySelector('.eyebrow').textContent='你的账号，你的独立会话';
    document.querySelector('.account-card .section-title p').textContent='学校网页登录 · 本机加密保存，按需恢复';
    document.querySelector('.account-card .help').textContent='登录后可在页面中完成学校验证码或二次验证。学校密码仅用于本次登录；开启记住登录后，次日会尝试恢复，学校会话失效时需重登。';
    document.querySelector('.privacy-note p').textContent='开启记住登录后，加密会话包保存在此浏览器；后端按需解密到独立内存。位置需重新确认。服务器会处理学校凭据，请仅在测试组可信服务上登录。';
    $('forget').textContent='清除学校会话与本机保存';
    document.querySelector('footer span').textContent='☾ 测试组专用 · HTTPS';
    const disclaimer=document.querySelector('.disclaimer p');
    disclaimer.textContent=disclaimer.textContent.replace('账号会话仅在本机保存，请勿共用或上传私人数据。','学校会话可加密保存在此浏览器，使用时由服务器处理；共用设备请关闭记住登录并在结束后清除会话。');
  }
  remoteActive=['opening','waiting','verifying'].includes(state.login.state);
  if(remoteActive&&!loginDialog.open)loginDialog.showModal();
  if(!remoteActive){loginDialog.close();remoteImage.removeAttribute('src');remoteImage.hidden=true;$('remote-text').value='';}
  $('remote-login-state').textContent=state.login.message;
});
setInterval(async()=>{
  if(!remoteActive||frameBusy||document.hidden)return;
  frameBusy=true;
  try{const frame=await api('/api/login/frame');if(remoteActive&&frame.image){remoteImage.src=frame.image;remoteImage.hidden=false;}}
  catch(e){$('remote-login-state').textContent=e.message;}
  finally{frameBusy=false;}
},1000);
