/* ============================================================================
 * phix 账号界面 ↔ Pinghe Launcher Lite 的桥（宿主适配层）
 * ----------------------------------------------------------------------------
 * `ui/account-ui.js` 是界面（与 PH Launcher 共用**同一份文件**，字节相同），
 * 这一份只做翻译：把 AccountUI 要的 CONTRACT 映射到 PHL Lite 已有的 `pywebview.api`
 * 上（`phix_*` 系列 + 新增的 `phix_prepare_*`）。
 *
 * 界面里一句平台相关的话都没有 —— 换宿主只需要换这个文件。
 * ========================================================================== */
(function () {
  'use strict';

  const PLATFORMS = [
    { id: 'edupage', label: 'EduPage' },
    { id: 'managebac', label: 'ManageBac' },
    { id: 'mail', label: '平和邮箱' },
    { id: 'xinlv', label: '心履' },
  ];

  async function call(name, ...args) {
    const r = await window.pywebview.api[name](...args);
    if (!r || r.ok !== true) throw new Error((r && (r.error || r.detail)) || '调用失败');
    return r.data !== undefined ? r.data : r;
  }

  async function safe(name, ...args) {
    try { return await call(name, ...args); } catch { return null; }
  }

  /** 共享 settings.yaml 里的账号字段 → 各平台登录情况。 */
  async function accountSites() {
    const doc = (await safe('settings_get')) || {};
    return {
      edupage: { username: doc.edupage_username || '' },
      managebac: { username: doc.managebac_email || '' },
      mail: { username: doc.mail_email || '' },
      xinlv: { username: doc.xinlv_username || '' },
    };
  }

  async function status() {
    const raw = (await safe('phix_status')) || {};
    // 后端已经在 phix_status 里把令牌接回来了，这里只取展示需要的字段
    const sites = await accountSites();
    const profile = (await safe('phix_profile_get')) || {};
    const accounts = PLATFORMS.map((p) => {
      const entry = sites[p.id] || {};
      const username = String(entry.username || entry.account || '');
      const saved = Boolean(username);
      return {
        id: p.id,
        label: p.label,
        loggedIn: saved,
        account: username,
        detail: saved ? '已保存登录信息' : '未登录',
      };
    });
    return {
      loggedIn: Boolean(raw.logged_in || raw.has_access_token || raw.has_refresh_token || raw.has_token),
      username: String(raw.username || ''),
      displayName: String(profile.display_name || raw.username || ''),
      avatar: String(profile.avatar || ''),
      server: String(raw.server || ''),
      syncedAt: String(raw.last_sync_at || ''),
      accounts,
    };
  }

  /** 后端已经有专门的探测（phix_sync_probe）：直接用它给的 local/remote 数字。 */
  async function probe() {
    const p = (await safe('phix_sync_probe')) || {};
    const objects = p.objects && typeof p.objects === 'object' ? p.objects : {};
    const list = Object.keys(objects).map((name) => ({
      id: name,
      label: name,
      remote: Number(objects[name] && objects[name].remote) || 0,
      local: Number(objects[name] && objects[name].local) || 0,
    }));
    let remote = 0;
    let local = 0;
    for (const o of list) {
      if (o.remote > 0) remote += 1;
      if (o.local > 0) local += 1;
    }
    return { remote: { count: remote }, local: { count: local }, objects: list };
  }

  const bridge = {
    status,
    probe,
    async login(username, password) {
      try {
        // 服务器由程序自己探测，界面不给用户填（与 PHL 一致）
        await call('phix_login', '', username, password, '');
        return { ok: true };
      } catch (error) {
        return { ok: false, error: String((error && error.message) || error) };
      }
    },
    async register(username, password) {
      try {
        await call('phix_register', '', username, password, 'password');
        return { ok: true };
      } catch (error) {
        return { ok: false, error: String((error && error.message) || error) };
      }
    },
    async logout() {
      try { await call('phix_logout'); } catch { /* 退出失败也让界面刷新 */ }
      return { ok: true };
    },
    /** direction: 'local' = 本地覆盖云端；'cloud' = 云端覆盖本地。 */
    async sync(direction) {
      try {
        const prefer = direction === 'cloud' ? 'remote' : 'local';
        await call('phix_sync', true, prefer);
        return { ok: true };
      } catch (error) {
        return { ok: false, error: String((error && error.message) || error) };
      }
    },
    async preparePlan() {
      return (await safe('phix_prepare_plan')) || [];
    },
    async prepareStep(id) {
      try {
        return (await call('phix_prepare_step', id)) || { ok: true };
      } catch (error) {
        return { ok: false, detail: String((error && error.message) || error).slice(0, 60) };
      }
    },
    async saveProfile(patch) {
      try {
        await call('phix_profile_save', JSON.stringify(patch || {}));
        return { ok: true };
      } catch { return { ok: false }; }
    },
    openRegister() {
      // 注册就在同一套界面里，不需要跳外链
    },
  };

  window.AccountBridge = bridge;
  if (window.AccountUI) window.AccountUI.init({ bridge });

  // 设置页里旧的「账号 + 密码」小框由占满窗口的那套界面取代，这里把它藏起来，
  // 免得设置页里同时出现两套登录入口。
  document.addEventListener('DOMContentLoaded', () => {
    const legacy = document.getElementById('phix-login-box');
    if (legacy) legacy.hidden = true;
  });
})();
