'use strict';
(() => {
  let owner = '', latest, record, initialized = false, running = false, enabled = true;
  let generation = 0, savedRevision = '', attemptedWorkspace = '';
  const control = document.getElementById('remember-session-control');
  const checkbox = document.getElementById('remember-session');
  const message = document.getElementById('remember-session-message');
  const retry = document.getElementById('retry-session-restore');
  const storage = window.SchoolSessionStorage;
  function say(text, canRetry = false) { message.textContent = text; retry.hidden = !canRetry; }
  function expiryText(expires) { return new Date(expires * 1000).toLocaleString(); }
  async function write(value, epoch) {
    if (epoch !== generation) return;
    const mode = await storage.put(owner, value);
    if (epoch !== generation) return;
    record = value;
    return mode;
  }
  async function clearSaved(remember) {
    const epoch = ++generation;
    record = {remember};
    savedRevision = latest && latest.persistence.revision;
    try { await write(record, epoch); }
    catch (_) { say('本机保存无法清除；服务器已撤销的会话包不能再次恢复。'); }
  }
  async function work() {
    if (running || !latest || !latest.hosted) return;
    running = true;
    const state = latest;
    let epoch;
    try {
      if (owner !== state.persistence.owner) {
        owner = state.persistence.owner; initialized = false; ++generation;
        record = null; savedRevision = ''; attemptedWorkspace = '';
      }
      epoch = generation;
      if (!initialized) {
        const result = await storage.get(owner);
        if (epoch !== generation) return;
        record = result.record; enabled = !record || record.remember;
        checkbox.checked = enabled; initialized = true;
        control.hidden = false;
        document.getElementById('remember-session-duration').textContent =
          `最多 ${Math.round(state.persistence.max_age / 3600 * 10) / 10} 小时，学校失效时仍需重登`;
        if (!result.available) say('本地存储不可用；本次登录可继续使用，无法保证下次恢复。');
        else say(enabled ? (record && record.token ? '已找到本机保存，准备恢复学校登录…' :
          '此浏览器未找到可恢复的学校登录；登录后将加密保存，共用设备请关闭。') : '未在此浏览器记住登录。');
      }
      const active = ['opening', 'waiting', 'verifying'].includes(state.login.state);
      if (!enabled || active) return;
      if (record && record.token && record.expires_at * 1000 <= Date.now()) {
        await write({remember: true}, epoch);
        say('保存的登录已到期，请重新登录。');
      }
      if (state.account && savedRevision !== state.persistence.revision) {
        savedRevision = state.persistence.revision;
        const result = await api('/api/session/package', {});
        if (epoch !== generation) return;
        const mode = await write({remember: true, token: result.token, expires_at: result.expires_at}, epoch);
        if (epoch === generation) say(`已加密保存在此浏览器（${mode}），最晚 ${expiryText(result.expires_at)} 到期。`);
      } else if (!state.account && record && record.token && attemptedWorkspace !== state.csrf) {
        attemptedWorkspace = state.csrf;
        say('正在恢复并验证学校登录…');
        const result = await api('/api/session/restore', {token: record.token});
        if (epoch !== generation) return;
        savedRevision = result.revision;
        await write({remember: true, token: result.token, expires_at: result.expires_at}, epoch);
        say(`学校登录已恢复；请重新确认实际位置。最晚 ${expiryText(result.expires_at)} 到期。`);
        await refresh();
      }
    } catch (error) {
      if (epoch !== generation) return;
      const permanent = ['saved_session_invalid', 'saved_session_expired', 'saved_session_unavailable',
        'session_rejected', 'portal_session_rejected', 'micro_app_unauthorized'].includes(error.reason);
      if (permanent) await clearSaved(true);
      say(error.message, !permanent);
    } finally {
      running = false;
      // A status received while storage/network work was pending must not be lost.
      if (latest !== state) work();
    }
  }
  document.addEventListener('workspace-status', event => { latest = event.detail; work(); });
  checkbox.addEventListener('change', async () => {
    enabled = checkbox.checked;
    checkbox.disabled = true;
    await clearSaved(enabled);
    try {
      if (!enabled) {
        await api('/api/session/package/forget', {});
        say('已撤销此浏览器保存的登录，本次工作台仍可使用。');
      } else {
        savedRevision = ''; attemptedWorkspace = '';
        say('已开启；登录后会加密保存到此浏览器。');
      }
      await refresh();
    } catch (error) { say(error.message + ' 请重试清除，或使用“清除学校会话与本机保存”。'); }
    finally { checkbox.disabled = false; work(); }
  });
  retry.addEventListener('click', () => { attemptedWorkspace = ''; savedRevision = ''; refresh().catch(error => say(error.message, true)); });
  document.addEventListener('school-session-cleared', () => { clearSaved(enabled); say('学校会话与此浏览器保存已清除。'); });
  document.addEventListener('school-session-invalid', () => { clearSaved(enabled); say('学校登录已失效，请重新登录。'); });
  window.addEventListener('saved-session-external-change', async event => {
    if (event.detail !== owner) return;
    const epoch = ++generation;
    const result = await storage.get(owner);
    if (epoch !== generation) return;
    record = result.record;
    enabled = !record || record.remember; checkbox.checked = enabled;
    savedRevision = latest && latest.persistence.revision;
    attemptedWorkspace = '';
    say(record && record.token ? '另一标签页已更新此浏览器保存的登录。' : '另一标签页已清除或关闭登录保存。');
    if (!document.hidden) refresh().catch(() => {});
  });
})();
