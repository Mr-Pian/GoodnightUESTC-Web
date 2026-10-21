'use strict';
// At most eight small records per origin. No plaintext school credentials.
window.SchoolSessionStorage = (() => {
  const prefix = 'uestc.saved.v1.', maximum = 8;
  let database;
  function valid(record) {
    return record && typeof record.remember === 'boolean' && Number.isFinite(record.updated)
      && (!record.token || (typeof record.token === 'string' && record.token.length <= 65536
        && Number.isFinite(record.expires_at)));
  }
  function open() {
    if (database) return database;
    database = new Promise((resolve, reject) => {
      let settled = false;
      const timer = setTimeout(() => finish(new Error('storage unavailable')), 1500);
      function finish(error, db) {
        if (settled) { if (db) db.close(); return; }
        settled = true; clearTimeout(timer);
        if (error) reject(error); else resolve(db);
      }
      try {
        const request = indexedDB.open('uestc-saved-session-v1', 1);
        request.onupgradeneeded = () => request.result.createObjectStore('sessions');
        request.onsuccess = () => {
          const db = request.result;
          db.onversionchange = () => { db.close(); database = null; };
          finish(null, db);
        };
        request.onerror = request.onblocked = () => finish(new Error('storage unavailable'));
      } catch (_) { finish(new Error('storage unavailable')); }
    });
    const pending = database;
    return pending.catch(error => {
      // A temporary storage failure must not disable IndexedDB until reload.
      if (database === pending) database = null;
      throw error;
    });
  }
  async function idb(operation, key, value) {
    const db = await open();
    return new Promise((resolve, reject) => {
      let result, transaction, settled = false;
      const timer = setTimeout(() => {
        if (settled) return;
        settled = true;
        if (transaction) { try { transaction.abort(); } catch (_) {} }
        reject(new Error('storage unavailable'));
      }, 1500);
      function finish(error) {
        if (settled) return;
        settled = true; clearTimeout(timer);
        if (error) reject(new Error('storage unavailable')); else resolve(result);
      }
      try {
        transaction = db.transaction('sessions', operation === 'get' ? 'readonly' : 'readwrite');
        const store = transaction.objectStore('sessions');
        const request = operation === 'get' ? store.get(key) : store.put(value, key);
        request.onsuccess = () => { result = request.result; };
        if (operation !== 'get') {
          const entries = [];
          const cursor = store.openCursor();
          cursor.onsuccess = () => {
            const row = cursor.result;
            if (row) { entries.push({key: row.key, updated: row.value.updated || 0}); row.continue(); }
            else {
              entries.sort((a, b) => b.updated - a.updated);
              entries.slice(maximum).forEach(entry => store.delete(entry.key));
            }
          };
        }
        transaction.oncomplete = () => finish();
        transaction.onerror = transaction.onabort = () => finish(true);
      } catch (_) { finish(true); }
    });
  }
  function localRead(key) {
    const raw = localStorage.getItem(prefix + key);
    if (!raw || raw.length > 66000) return null;
    const record = JSON.parse(raw);
    return valid(record) ? record : null;
  }
  function notify(owner) {
    window.dispatchEvent(new CustomEvent('saved-session-changed', {detail: owner}));
    // This notification contains no token. It also works without BroadcastChannel.
    try { localStorage.setItem(prefix + 'notice', JSON.stringify({owner, time: Date.now()})); } catch (_) {}
  }
  async function get(owner) {
    const candidates = [];
    let available = false;
    try { const value = await idb('get', owner); available = true; if (valid(value)) candidates.push(value); } catch (_) {}
    try { const value = localRead(owner); available = true; if (value) candidates.push(value); } catch (_) {}
    candidates.sort((a, b) => b.updated - a.updated);
    return {record: candidates[0] || null, available};
  }
  async function put(owner, value) {
    const record = {...value, updated: Date.now()};
    if (!valid(record)) throw new Error('invalid storage record');
    try {
      await idb('put', owner, record);
      try { localStorage.removeItem(prefix + owner); } catch (_) {}
      notify(owner);
      return 'IndexedDB';
    } catch (_) {}
    try {
      localStorage.setItem(prefix + owner, JSON.stringify(record));
      const entries = [];
      for (let i = 0; i < localStorage.length; i++) {
        const key = localStorage.key(i);
        if (key && key.startsWith(prefix) && key !== prefix + 'notice') {
          let updated = 0;
          try { updated = JSON.parse(localStorage.getItem(key)).updated || 0; } catch (_) {}
          entries.push({key, updated});
        }
      }
      entries.sort((a, b) => b.updated - a.updated);
      entries.slice(maximum).forEach(entry => localStorage.removeItem(entry.key));
      notify(owner);
      return 'localStorage';
    } catch (_) { throw new Error('此浏览器无法保存登录；本次会话仍可使用，关闭或过期后需重新登录。'); }
  }
  window.addEventListener('storage', event => {
    if (event.key !== prefix + 'notice' || !event.newValue) return;
    try { window.dispatchEvent(new CustomEvent('saved-session-external-change', {detail: JSON.parse(event.newValue).owner})); } catch (_) {}
  });
  return {get, put};
})();
