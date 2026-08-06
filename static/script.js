const userInput = document.getElementById('userInput');
const submitBtn = document.getElementById('submitBtn');
const messagesContainer = document.getElementById('messagesContainer');
const logoContainer = document.getElementById('logoContainer');
const thinkToggle = document.getElementById('thinkToggle');
const webToggle = document.getElementById('webToggle');

let isThinkMode = false;
let isWebMode = false;
let isGenerating = false;
let chatMessages = [];
let currentChatId = null;
let isFirstMessage = true;
let isAgentMode = false;
let currentUserId = localStorage.getItem('pepperUserId') || null;
let currentUsername = localStorage.getItem('pepperUsername') || null;

function authenticatedHeaders(headers = {}) {
    const token = localStorage.getItem('pepperSession');
    const result = { ...headers };
    if (token && token !== 'cookie') {
        result['Authorization'] = `Bearer ${token}`;
    }
    return result;
}

// JWTs used by older releases must not remain script-readable.
localStorage.removeItem('pepperJwt');

// Hide sidebar immediately if not logged in (before any async auth)
(function() {
    const token = localStorage.getItem('pepperSession');
    if (!token) {
        document.body.classList.add('is-guest');
    }
})();
let currentAbortController = null;
let isPaused = false;
let pausedMsgIndex = null;
let guestLoginPromptForced = false;
let lastGuestPromptPulseAt = 0;
let supportsThinkMode = false;
let selectedSkillType = '';

const agentModeBtn = document.getElementById('agentModeBtn');
const guestLimitBanner = document.getElementById('guestLimitBanner');
const guestLimitBannerText = document.getElementById('guestLimitBannerText');
const guestLimitLoginBtn = document.getElementById('guestLimitLoginBtn');
const guestLimitRegisterBtn = document.getElementById('guestLimitRegisterBtn');
const guestAuthRequiredModal = document.getElementById('guestAuthRequiredModal');
const closeGuestAuthModalBtn = document.getElementById('closeGuestAuthModalBtn');
const appContainer = document.querySelector('.app-container');
const inputWrapper = document.querySelector('.input-wrapper');
const composerInput = document.querySelector('.composer-input');
const COMPOSER_MAX_HEIGHT = 168;
let guestAuthReturnFocus = null;
let googleOAuthClientId = '685645444928-ivt7lgsjiatv0ff0r68ckmbln1rdrrm4.apps.googleusercontent.com';
let googleConnectorOAuthClientId = googleOAuthClientId;
let googleLoginRedirectUriValue = '';

function googleLoginRedirectUri() {
    return googleLoginRedirectUriValue || new URL('/', window.location.origin).href;
}

function isMobileLayout() {
    return window.innerWidth <= 760 || (
        window.innerHeight <= 500
        && window.matchMedia?.('(hover: none) and (pointer: coarse)').matches
    );
}

// A phone in landscape can be wider than the desktop breakpoint but still has
// too little vertical room for an anchored, down-facing menu. Keep this
// narrowly scoped to the response-mode picker so the rest of the layout keeps
// its established landscape behavior.
function isMobileModePickerLayout() {
    return isMobileLayout();
}

function syncMobileViewportMetrics() {
    const viewport = window.visualViewport;
    const height = Math.round(viewport?.height || window.innerHeight || document.documentElement.clientHeight || 0);
    const width = Math.round(viewport?.width || window.innerWidth || document.documentElement.clientWidth || 0);
    if (height) document.documentElement.style.setProperty('--app-height', `${height}px`);
    if (width) document.documentElement.style.setProperty('--app-width', `${width}px`);
    document.documentElement.style.setProperty('--vv-offset-top', `${Math.max(0, Math.round(viewport?.offsetTop || 0))}px`);
}

function syncComposerHeightVar() {
    if (!composerInput) return;
    const height = Math.round(composerInput.getBoundingClientRect().height || 58);
    document.documentElement.style.setProperty('--composer-height', `${height}px`);
}

// The composer grows when text wraps or attachments are added. Keep the
// layout variable tied to its real rendered height so the last message is
// never hidden behind the fixed composer.
if (composerInput && typeof ResizeObserver !== 'undefined') {
    const composerResizeObserver = new ResizeObserver(() => {
        window.requestAnimationFrame(syncComposerHeightVar);
    });
    composerResizeObserver.observe(composerInput);
}
window.requestAnimationFrame(syncComposerHeightVar);

syncMobileViewportMetrics();
if (window.visualViewport) {
    window.visualViewport.addEventListener('resize', syncMobileViewportMetrics);
    window.visualViewport.addEventListener('scroll', syncMobileViewportMetrics);
}
window.addEventListener('resize', syncMobileViewportMetrics);
window.addEventListener('orientationchange', () => {
    setTimeout(syncMobileViewportMetrics, 80);
    setTimeout(syncMobileViewportMetrics, 280);
});

// Non-dialog mobile surfaces participate in browser history. Native dialogs
// already handle Escape/back themselves, so only the drawer and full-screen
// overlays are registered here.
const MOBILE_OVERLAY_HISTORY_KEY = '__bisnesMobileOverlay';
const mobileOverlayClosers = new Map();
let activeMobileHistoryOverlay = '';

function registerMobileHistoryOverlay(name, closeWithoutHistory) {
    if (name && typeof closeWithoutHistory === 'function') {
        mobileOverlayClosers.set(name, closeWithoutHistory);
    }
}

function mobileOverlayState(name) {
    return { ...(window.history.state || {}), [MOBILE_OVERLAY_HISTORY_KEY]: name };
}

function markMobileHistoryOverlayOpen(name) {
    if (!name || !isMobileLayout()) return;
    const stateName = window.history.state?.[MOBILE_OVERLAY_HISTORY_KEY] || '';
    const previousName = activeMobileHistoryOverlay || stateName;

    if (previousName && previousName !== name) {
        mobileOverlayClosers.get(previousName)?.();
        window.history.replaceState(mobileOverlayState(name), '', window.location.href);
    } else if (stateName !== name) {
        window.history.pushState(mobileOverlayState(name), '', window.location.href);
    }
    activeMobileHistoryOverlay = name;
}

function markMobileHistoryOverlayClosed(name, { fromHistory = false } = {}) {
    if (!name) return;
    if (activeMobileHistoryOverlay === name) activeMobileHistoryOverlay = '';
    if (!fromHistory && window.history.state?.[MOBILE_OVERLAY_HISTORY_KEY] === name) {
        window.history.back();
    }
}

window.addEventListener('popstate', (event) => {
    const stateName = event.state?.[MOBILE_OVERLAY_HISTORY_KEY] || '';
    if (activeMobileHistoryOverlay && stateName !== activeMobileHistoryOverlay) {
        const closingName = activeMobileHistoryOverlay;
        activeMobileHistoryOverlay = '';
        mobileOverlayClosers.get(closingName)?.();
    }

    // A forward navigation must not resurrect an already dismissed overlay.
    // Remove only our marker while retaining any state owned by other code.
    if (stateName && !activeMobileHistoryOverlay) {
        const cleanState = { ...(event.state || {}) };
        delete cleanState[MOBILE_OVERLAY_HISTORY_KEY];
        window.history.replaceState(cleanState, '', window.location.href);
    }
});

// The UI ships English and Bahasa Melayu only. Chinese was retired; anything
// else (including a stored 'zh' from before the change) resolves to English.
const SUPPORTED_LANGS = ['en', 'ms'];
const DEFAULT_LANG = 'en';

function normalizeLang(value) {
    return SUPPORTED_LANGS.includes(value) ? value : DEFAULT_LANG;
}

function getBrowserLanguagePreference() {
    const raw = (navigator.language || navigator.userLanguage || 'en').toLowerCase();
    if (raw.startsWith('ms') || raw.includes('my')) return 'ms';
    return DEFAULT_LANG;
}

function getPreferredLanguage() {
    // Single read path for the whole app, so the migration below covers every
    // caller: a user who had picked Chinese silently lands on English instead
    // of requesting a locale file that no longer exists.
    const stored = localStorage.getItem('pepperLang');
    const resolved = stored ? normalizeLang(stored) : getBrowserLanguagePreference();
    if (stored && stored !== resolved) {
        localStorage.setItem('pepperLang', resolved);
    }
    return resolved;
}

function passwordMeetsPolicy(password) {
    const value = String(password || '');
    return value.length >= 8 && /^[A-Z]/.test(value) && /[^\w\s]/.test(value);
}

function resolveAvatarSrc(url) {
    if (!url) return '';
    try {
        const parsed = new URL(url);
        if (parsed.hostname === 'lh3.googleusercontent.com' || parsed.hostname.endsWith('.googleusercontent.com')) {
            return `/api/avatar/google?url=${encodeURIComponent(url)}`;
        }
    } catch {
        return url;
    }
    return url;
}

function escapeAttr(value) {
    return String(value || '').replace(/[&<>"']/g, ch => ({
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#39;'
    }[ch]));
}

function escapeHtml(value) {
    return escapeAttr(value);
}

function safeExternalUrl(value) {
    try {
        const url = new URL(String(value || ''), window.location.origin);
        return ['http:', 'https:'].includes(url.protocol) ? url.href : '';
    } catch {
        return '';
    }
}

function tUi(key, fallback = '') {
    if (typeof i18next !== 'undefined' && i18next?.isInitialized) {
        const value = i18next.t(key);
        if (value && value !== key) return value;
    }
    const bundleValue = window._pepperLang?.[key];
    return bundleValue || fallback || key;
}

function recentFilesStateMarkup(key, fallback) {
    return `<div class="recent-files-state" data-i18n="${escapeAttr(key)}">${escapeAttr(tUi(key, fallback))}</div>`;
}

async function loadPublicConfig() {
    try {
        const res = await fetch('/api/public-config', { cache: 'no-store' });
        if (!res.ok) return;
        const data = await res.json();
        const loginClientId = data.google_login_oauth_client_id || data.google_oauth_client_id;
        if (loginClientId) {
            googleOAuthClientId = loginClientId;
        }
        if (data.google_login_redirect_uri) {
            googleLoginRedirectUriValue = data.google_login_redirect_uri;
        }
        googleConnectorOAuthClientId = data.google_connector_oauth_client_id || googleOAuthClientId;
        if (typeof data.supports_think_mode === 'boolean') {
            supportsThinkMode = data.supports_think_mode;
            if (!supportsThinkMode) {
                isThinkMode = false;
            }
            applyThinkModeAvailability();
            updateTogglesUI();
        }
    } catch (err) {
        console.warn('Failed to load public config', err);
    }
}
const publicConfigReady = loadPublicConfig();

function applyThinkModeAvailability() {
    if (!thinkToggle) return;
    // Use style.display directly — CSS `display:flex` on .toggle-switch overrides [hidden]
    const showThinkToggle = supportsThinkMode && !isAgentMode;
    thinkToggle.style.display = showThinkToggle ? '' : 'none';
    thinkToggle.disabled = !showThinkToggle;
    thinkToggle.setAttribute('aria-hidden', showThinkToggle ? 'false' : 'true');
    if (!showThinkToggle) {
        thinkToggle.classList.remove('active');
    }
}

function syncComposerModeControls() {
    const hiddenUploadBtn = document.getElementById('uploadBtn');
    const toggles = document.querySelector('.toggles-container');
    const imgBtn = document.getElementById('imgUploadBtn');
    const cc = document.getElementById('connectorsContainer');
    const uploadMenu = document.getElementById('uploadMenuShell');

    if (hiddenUploadBtn) hiddenUploadBtn.style.display = 'none';
    document.body.classList.toggle('agent-mode', Boolean(isAgentMode));
    if (toggles) toggles.style.display = '';
    if (cc) cc.style.display = isAgentMode ? 'flex' : 'none';
    if (uploadMenu) {
        uploadMenu.classList.toggle('agent-mode', isAgentMode);
        if (!isAgentMode) uploadMenu.classList.remove('skills-open');
    }
    if (!isAgentMode && selectedSkillType) clearSelectedSkillType();
    if (imgBtn) {
        const uploadMenuTitle = tUi('uploadMenuTitle', 'Open upload menu');
        imgBtn.title = uploadMenuTitle;
        imgBtn.setAttribute('aria-label', uploadMenuTitle);
    }
    applyThinkModeAvailability();
    window.requestAnimationFrame(syncComposerHeightVar);
}

function resizeComposer() {
    if (!userInput) return;
    userInput.style.height = 'auto';
    const nextHeight = Math.min(userInput.scrollHeight, COMPOSER_MAX_HEIGHT);
    userInput.style.setProperty('--composer-textarea-height', `${nextHeight}px`);
    userInput.style.height = `${nextHeight}px`;
    userInput.style.overflowY = userInput.scrollHeight > COMPOSER_MAX_HEIGHT ? 'auto' : 'hidden';
    if (composerInput) {
        composerInput.classList.toggle('composer-expanded', nextHeight > 34);
    }
    requestAnimationFrame(syncComposerHeightVar);
}

function getUiCopy() {
    return window._pepperLang || {
        greeting: 'How can I help you today?',
        loginBtn: 'Sign in',
        registerBtn: 'Sign up',
        guestLimitText: 'Get smarter responses, upload files and images, and unlock more features.',
        guestLimitRegisterBtn: 'Sign up for free',
        agentRequiresLogin: 'Login required for Agent mode'
    };
}

function getTimeGreeting(lang) {
    const h = new Date().getHours();
    if (lang === 'ms') {
        if (h < 12) return 'Selamat pagi! Apa yang boleh saya bantu?';
        if (h < 18) return 'Selamat petang! Ada apa yang anda perlukan?';
        return 'Selamat malam! Ada apa yang boleh saya bantu?';
    }
    if (h < 12) return 'Good morning. What\'s on your mind?';
    if (h < 18) return 'Good afternoon. How can I help?';
    return 'Good evening. What can I help you with?';
}

function getNormalLandingMarkup() {
    const copy = getUiCopy();
    const greeting = copy.greeting || 'How can I help you today?';
    return `<h2><span class="logo-text">bisnes.ai</span><br/>${greeting}</h2>`;
}

function updateGuestLimitBannerCopy() {
    const t = getUiCopy();
    if (guestLimitBannerText) guestLimitBannerText.textContent = t.guestLimitText;
    if (guestLimitLoginBtn) guestLimitLoginBtn.textContent = t.loginBtn;
    if (guestLimitRegisterBtn) guestLimitRegisterBtn.textContent = t.guestLimitRegisterBtn || t.registerBtn;
    if (agentModeBtn) agentModeBtn.title = currentUserId ? 'AI Agent' : (t.agentRequiresLogin || 'Login required for Agent mode');
}

function syncPreferenceControls() {
    const lang = getPreferredLanguage();
    document.querySelectorAll('.lang-btn').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.lang === lang);
    });
    window.dispatchEvent(new CustomEvent('mof-preferences-changed', {
        detail: { language: lang }
    }));
}

function applyStoredPreferences(preferences = {}) {
    if (preferences.language) localStorage.setItem('pepperLang', preferences.language);
    const lang = getPreferredLanguage();
    if (window.applyPepperLang) window.applyPepperLang(lang);
    syncPreferenceControls();
}

function storeAccountProfileFields(data = {}) {
    if (Object.prototype.hasOwnProperty.call(data, 'phone')) {
        if (data.phone) localStorage.setItem('pepperPhone', data.phone);
        else localStorage.removeItem('pepperPhone');
    }
    if (Object.prototype.hasOwnProperty.call(data, 'country_code') || Object.prototype.hasOwnProperty.call(data, 'phone_country_code')) {
        const countryCode = data.country_code || data.phone_country_code;
        if (countryCode) localStorage.setItem('pepperPhoneCountryCode', countryCode);
        else localStorage.removeItem('pepperPhoneCountryCode');
    }
    if (Object.prototype.hasOwnProperty.call(data, 'country_iso')) {
        if (data.country_iso) localStorage.setItem('pepperCountryIso', data.country_iso);
        else localStorage.removeItem('pepperCountryIso');
    }
    if (Object.prototype.hasOwnProperty.call(data, 'region')) {
        if (data.region) localStorage.setItem('pepperRegion', data.region);
        else localStorage.removeItem('pepperRegion');
    }
    if (Object.prototype.hasOwnProperty.call(data, 'requires_profile_completion')) {
        localStorage.setItem('pepperRequiresProfileCompletion', data.requires_profile_completion ? 'true' : 'false');
    }
    if (Object.prototype.hasOwnProperty.call(data, 'business_category')) {
        if (data.business_category) localStorage.setItem('pepperBusinessCategory', data.business_category);
        else localStorage.removeItem('pepperBusinessCategory');
    }
    if (Object.prototype.hasOwnProperty.call(data, 'business_nature')) {
        if (data.business_nature) localStorage.setItem('pepperBusinessNature', data.business_nature);
        else localStorage.removeItem('pepperBusinessNature');
    }
    if (Object.prototype.hasOwnProperty.call(data, 'requires_business_profile_completion')) {
        localStorage.setItem('pepperRequiresBusinessProfileCompletion', data.requires_business_profile_completion ? 'true' : 'false');
    }
}

function handleSessionExpired(options = {}) {
    const { redirect = true } = options;
    clearStoredAccountFields();
    currentUserId = null;
    currentUsername = null;
    document.body.classList.add('is-guest');
    document.body.classList.remove('is-logged-in');
    syncGuestAccessState();
    if (redirect && !window.location.pathname.endsWith('/static/login.html')) {
        window.location.href = '/static/login.html';
    }
}

function clearStoredAccountFields() {
    [
        'pepperSession',
        'pepperUserId',
        'pepperUsername',
        'pepperDisplayName',
        'pepperAvatar',
        'pepperPhone',
        'pepperPhoneCountryCode',
        'pepperCountryIso',
        'pepperCountry',
        'pepperRegion',
        'pepperRequiresProfileCompletion',
        'pepperBusinessCategory',
        'pepperBusinessNature',
        'pepperRequiresBusinessProfileCompletion',
        'pepperGoogleFullscreenAuth',
        'pepperGoogleEmail',
        'pepperGoogleLinked',
        'pepperAuthProvider',
        'pepperHasPassword',
        'pepperCreatedAt',
        'pepperGuestQuestionCount'
    ].forEach(key => localStorage.removeItem(key));
    sessionStorage.removeItem('pepperCompleteGoogleProfile');
    sessionStorage.removeItem('pepperCompleteBusinessProfile');
    sessionStorage.removeItem('pepperSkipGoogleProfileCompletion');
    sessionStorage.removeItem('pepperGoogleFullscreenAuth');
}

async function saveUserPreferences(partial = {}) {
    const token = localStorage.getItem('pepperSession');
    if (!token) return;
    const body = {
        language: getPreferredLanguage(),
        ...partial
    };
    try {
        const res = await fetch('/api/account/preferences', {
            method: 'PUT',
            credentials: 'same-origin',
            headers: {
                'Content-Type': 'application/json',
                ...authenticatedHeaders(),
            },
            body: JSON.stringify(body)
        });
        if (res.status === 401) {
            handleSessionExpired();
            return;
        }
        if (res.ok) {
            const data = await res.json();
            if (data.preferences) applyStoredPreferences(data.preferences);
        }
    } catch (err) {
        console.warn('Failed to save user preferences', err);
    }
}

async function loadUserPreferences() {
    const token = localStorage.getItem('pepperSession');
    if (!token) return;
    try {
        const res = await fetch('/api/account/preferences', {
            credentials: 'same-origin',
            headers: authenticatedHeaders(),
        });
        if (res.status === 401) {
            handleSessionExpired();
            return;
        }
        if (!res.ok) return;
        const data = await res.json();
        if (data.username) localStorage.setItem('pepperUsername', data.username);
        if (data.display_name) localStorage.setItem('pepperDisplayName', data.display_name);
        if (data.avatarUrl) localStorage.setItem('pepperAvatar', data.avatarUrl);
        if (data.created_at) localStorage.setItem('pepperCreatedAt', data.created_at);
        localStorage.setItem('pepperHasPassword', data.has_password ? 'true' : 'false');
        localStorage.setItem('pepperAuthProvider', data.auth_provider || 'local');
        localStorage.setItem('pepperGoogleLinked', data.google_linked ? 'true' : 'false');
        if (data.google_email) localStorage.setItem('pepperGoogleEmail', data.google_email);
        else localStorage.removeItem('pepperGoogleEmail');
        storeAccountProfileFields(data);
        if (data.preferences) applyStoredPreferences(data.preferences);
        if (data.google_linked) fetchConnectorsStatus();
        else clearConnectorChecks();
        const sidebarName = document.getElementById('userDisplayName');
        if (sidebarName && data.display_name) sidebarName.textContent = data.display_name;
        if (data.requires_profile_completion) {
            if (hasSkippedProfileCompletionForSession()) {
                sessionStorage.removeItem('pepperCompleteGoogleProfile');
            } else {
                sessionStorage.setItem('pepperCompleteGoogleProfile', '1');
                if (!window.location.pathname.endsWith('/static/login.html')) {
                    window.location.href = '/static/login.html?complete=google';
                }
            }
        } else if (data.requires_business_profile_completion) {
            sessionStorage.removeItem('pepperCompleteGoogleProfile');
            sessionStorage.setItem('pepperCompleteBusinessProfile', '1');
            if (!window.location.pathname.endsWith('/static/login.html')) {
                window.location.href = '/static/login.html?complete=business';
            }
        } else {
            sessionStorage.removeItem('pepperCompleteGoogleProfile');
            sessionStorage.removeItem('pepperCompleteBusinessProfile');
            sessionStorage.removeItem(PROFILE_COMPLETION_SKIP_KEY);
        }
    } catch (err) {
        console.warn('Failed to load user preferences', err);
    }
}

const toastTranslations = {
    en: {
        'Google account linked.': 'Google account linked.',
        'Failed to link Google': 'Failed to link Google.',
        'Google connector authorization state mismatch. Please try again.': 'Google connector authorization state mismatch. Please try again.',
        'Your session expired during auth. Please log in again.': 'Your session expired during auth. Please log in again.',
        'Google Workspace connected.': 'Google Workspace connected.',
        'Connector Network Error': 'Connector network error.',
        'Google authorization failed. Please try again.': 'Google authorization failed. Please try again.',
        'Please login first.': 'Please log in first.',
        'Google OAuth loading... please wait.': 'Google sign-in is still loading. Please wait.',
        'PDF download failed. Please generate it again.': 'PDF download failed. Please generate it again.',
        'Image download failed. Please generate it again.': 'Image download failed. Please generate it again.',
        'Network error.': 'Network error. Please try again.',
        'Password must contain at least 8 characters': 'Password must have at least 8 characters, start with an uppercase letter, and include a special character.',
        'Password must start with an uppercase letter': 'Password must have at least 8 characters, start with an uppercase letter, and include a special character.',
        'Password must contain at least one special character': 'Password must have at least 8 characters, start with an uppercase letter, and include a special character.',
        'Unknown error': 'Unknown error',
        connectorAuthFailed: 'Connector authorization failed',
        genericError: 'Something went wrong. Please try again.'
    },
    zh: {
        'Google account linked.': 'Google 账号已绑定。',
        'Failed to link Google': '无法绑定 Google 账号。',
        'Google connector authorization state mismatch. Please try again.': 'Google 连接器授权状态不匹配，请重试。',
        'Your session expired during auth. Please log in again.': '登录会话已过期，请重新登录。',
        'Google Workspace connected.': 'Google Workspace 已连接。',
        'Connector Network Error': '连接器网络错误。',
        'Google authorization failed. Please try again.': 'Google 授权失败，请重试。',
        'Please login first.': '请先登录。',
        'Google OAuth loading... please wait.': 'Google 登录正在加载，请稍候。',
        'PDF download failed. Please generate it again.': 'PDF 下载失败，请重新生成。',
        'Image download failed. Please generate it again.': '图片下载失败，请重新生成。',
        'Network error.': '网络错误，请重试。',
        'Password must contain at least 8 characters': '密码至少需要 8 个字符，首位为大写字母，并包含一个特殊符号。',
        'Password must start with an uppercase letter': '密码至少需要 8 个字符，首位为大写字母，并包含一个特殊符号。',
        'Password must contain at least one special character': '密码至少需要 8 个字符，首位为大写字母，并包含一个特殊符号。',
        'Unknown error': '未知错误',
        connectorAuthFailed: '连接器授权失败',
        genericError: '操作失败，请重试。'
    },
    ms: {
        'Google account linked.': 'Akaun Google telah dipautkan.',
        'Failed to link Google': 'Tidak dapat memautkan akaun Google.',
        'Google connector authorization state mismatch. Please try again.': 'Status kebenaran penyambung Google tidak sepadan. Sila cuba lagi.',
        'Your session expired during auth. Please log in again.': 'Sesi log masuk anda tamat semasa pengesahan. Sila log masuk semula.',
        'Google Workspace connected.': 'Google Workspace telah disambungkan.',
        'Connector Network Error': 'Ralat rangkaian penyambung.',
        'Google authorization failed. Please try again.': 'Pengesahan Google gagal. Sila cuba lagi.',
        'Please login first.': 'Sila log masuk dahulu.',
        'Google OAuth loading... please wait.': 'Log masuk Google sedang dimuatkan. Sila tunggu.',
        'PDF download failed. Please generate it again.': 'Muat turun PDF gagal. Sila jana semula.',
        'Image download failed. Please generate it again.': 'Muat turun imej gagal. Sila jana semula.',
        'Network error.': 'Ralat rangkaian. Sila cuba lagi.',
        'Password must contain at least 8 characters': 'Kata laluan mesti sekurang-kurangnya 8 aksara, bermula dengan huruf besar dan mempunyai simbol khas.',
        'Password must start with an uppercase letter': 'Kata laluan mesti sekurang-kurangnya 8 aksara, bermula dengan huruf besar dan mempunyai simbol khas.',
        'Password must contain at least one special character': 'Kata laluan mesti sekurang-kurangnya 8 aksara, bermula dengan huruf besar dan mempunyai simbol khas.',
        'Unknown error': 'Ralat tidak diketahui',
        connectorAuthFailed: 'Kebenaran penyambung gagal',
        genericError: 'Sesuatu telah berlaku. Sila cuba lagi.'
    }
};

function localizeToastMessage(message) {
    const raw = String(message || '');
    const translations = toastTranslations[getPreferredLanguage()] || toastTranslations.en;
    if (Object.prototype.hasOwnProperty.call(translations, raw)) return translations[raw];

    const connectorAuthPrefix = 'Connector Auth Failed: ';
    if (raw.startsWith(connectorAuthPrefix)) {
        return `${translations.connectorAuthFailed}: ${localizeToastMessage(raw.slice(connectorAuthPrefix.length))}`;
    }
    if (getPreferredLanguage() !== 'en' && /^[\x00-\x7F]+$/.test(raw)) return translations.genericError;
    return raw;
}

function showToast(message, isError = false) {
    let stack = document.getElementById('mofToastStack');
    if (!stack) {
        stack = document.createElement('div');
        stack.id = 'mofToastStack';
        stack.className = 'mof-toast-stack';
        document.body.appendChild(stack);
    }
    const toast = document.createElement('div');
    toast.className = `mof-toast${isError ? ' error' : ''}`;
    toast.textContent = localizeToastMessage(message);
    stack.appendChild(toast);
    requestAnimationFrame(() => toast.classList.add('show'));
    setTimeout(() => {
        toast.classList.remove('show');
        setTimeout(() => toast.remove(), 220);
    }, 3400);
}
window.showToast = showToast;

function createGoogleOAuthState(prefix = 'google') {
    if (window.crypto && typeof window.crypto.randomUUID === 'function') {
        return `${prefix}-${window.crypto.randomUUID()}`;
    }
    return `${prefix}-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function cleanGoogleOAuthUrl() {
    const cleanUrl = `${window.location.origin}${window.location.pathname}`;
    window.history.replaceState({}, document.title, cleanUrl);
}

const GOOGLE_FULLSCREEN_AUTH_KEY = 'pepperGoogleFullscreenAuth';
const GOOGLE_AUTH_STATE_TTL_MS = 15 * 60 * 1000;
const PROFILE_COMPLETION_SKIP_KEY = 'pepperSkipGoogleProfileCompletion';

function storeGoogleFullscreenAuth(payload) {
    const value = JSON.stringify({
        ...payload,
        createdAt: Date.now()
    });
    sessionStorage.setItem(GOOGLE_FULLSCREEN_AUTH_KEY, value);
    localStorage.setItem(GOOGLE_FULLSCREEN_AUTH_KEY, value);
}

function readGoogleFullscreenAuth() {
    const raw = sessionStorage.getItem(GOOGLE_FULLSCREEN_AUTH_KEY) || localStorage.getItem(GOOGLE_FULLSCREEN_AUTH_KEY);
    if (!raw) return null;
    try {
        const payload = JSON.parse(raw);
        if (payload.createdAt && Date.now() - payload.createdAt > GOOGLE_AUTH_STATE_TTL_MS) {
            return null;
        }
        return payload;
    } catch {
        return null;
    }
}

function clearGoogleFullscreenAuth() {
    sessionStorage.removeItem(GOOGLE_FULLSCREEN_AUTH_KEY);
    localStorage.removeItem(GOOGLE_FULLSCREEN_AUTH_KEY);
}

function hasSkippedProfileCompletionForSession() {
    return sessionStorage.getItem(PROFILE_COMPLETION_SKIP_KEY) === '1';
}

async function handleGoogleFullscreenReturn() {
    const url = new URL(window.location.href);
    const hashParams = new URLSearchParams((window.location.hash || '').replace(/^#/, ''));
    const queryParams = url.searchParams;
    const accessToken = hashParams.get('access_token');
    const authCode = queryParams.get('code');
    const state = hashParams.get('state') || queryParams.get('state') || '';
    const error = hashParams.get('error') || queryParams.get('error');

    if (!accessToken && !authCode && !error) return;
    cleanGoogleOAuthUrl();

    if (error) {
        sessionStorage.setItem('pepperGoogleAuthError', `Google authorization failed: ${error}`);
        clearGoogleFullscreenAuth();
        sessionStorage.removeItem('pepperGoogleConnectorAuth');
        window.location.href = '/static/login.html';
        return;
    }

    await publicConfigReady;

    if (accessToken) {
        let stored = readGoogleFullscreenAuth();
        clearGoogleFullscreenAuth();
        if (!stored || stored.state !== state) {
            if (stored?.purpose === 'link') {
                sessionStorage.setItem('pepperGoogleAuthError', 'Google authorization state mismatch. Please try again.');
                window.location.href = '/static/login.html';
                return;
            }
            stored = {
                state,
                purpose: 'login',
                language: getPreferredLanguage(),
                returnPath: '/'
            };
        }

        if (stored.purpose === 'link') {
            const jwt = localStorage.getItem('pepperSession');
            if (!jwt) {
                window.location.href = '/static/login.html';
                return;
            }
            try {
                const res = await fetch('/api/account/link-google', {
                    method: 'POST',
                    headers: { 'Authorization': `Bearer ${jwt}`, 'Content-Type': 'application/json' },
                    body: JSON.stringify({ token: accessToken })
                });
                const data = await res.json().catch(() => ({}));
                if (!res.ok) throw new Error(data.detail || 'Failed to link Google');
                localStorage.setItem('pepperGoogleLinked', 'true');
                localStorage.setItem('pepperAuthProvider', data.auth_provider || localStorage.getItem('pepperAuthProvider') || 'local');
                if (data.google_email) localStorage.setItem('pepperGoogleEmail', data.google_email);
                sessionStorage.setItem('pepperOpenAccountAfterGoogle', '1');
                showToast('Google account linked.');
            } catch (err) {
                showToast(err.message || 'Failed to link Google', true);
            }
            return;
        }

        try {
            const res = await fetch('/api/auth/google', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    token: accessToken,
                    language: stored.language || getPreferredLanguage()
                })
            });
            const data = await res.json();
            if (!res.ok || data.status !== 'success') {
                throw new Error(data.detail || 'Google Login Failed');
            }
            localStorage.setItem('pepperSession', 'cookie');
            localStorage.setItem('pepperUserId', data.user_id);
            localStorage.setItem('pepperUsername', data.username);
            storeAccountProfileFields(data);
            if (data.display_name) localStorage.setItem('pepperDisplayName', data.display_name);
            if (data.preferences?.language) localStorage.setItem('pepperLang', data.preferences.language);
            if (data.avatarUrl) localStorage.setItem('pepperAvatar', data.avatarUrl);
            if (data.requires_profile_completion) {
                sessionStorage.removeItem(PROFILE_COMPLETION_SKIP_KEY);
                sessionStorage.setItem('pepperCompleteGoogleProfile', '1');
                window.location.href = '/static/login.html?complete=google';
                return;
            } else if (data.requires_business_profile_completion) {
                sessionStorage.removeItem('pepperCompleteGoogleProfile');
                sessionStorage.setItem('pepperCompleteBusinessProfile', '1');
                window.location.href = '/static/login.html?complete=business';
                return;
            } else {
                sessionStorage.removeItem('pepperCompleteGoogleProfile');
                sessionStorage.removeItem('pepperCompleteBusinessProfile');
                sessionStorage.removeItem(PROFILE_COMPLETION_SKIP_KEY);
            }
            document.body.classList.remove('is-guest');
            window.location.href = stored.returnPath || '/';
        } catch (err) {
            sessionStorage.setItem('pepperGoogleAuthError', err.message || 'Google Login Failed');
            window.location.href = '/static/login.html';
        }
        return;
    }

    if (authCode) {
        const raw = sessionStorage.getItem('pepperGoogleConnectorAuth');
        const stored = raw ? JSON.parse(raw) : null;
        sessionStorage.removeItem('pepperGoogleConnectorAuth');
        if (!stored || stored.state !== state || !stored.service) {
            showToast('Google connector authorization state mismatch. Please try again.', true);
            return;
        }
        const jwt = localStorage.getItem('pepperSession');
        if (!jwt) {
            window.location.href = '/static/login.html';
            return;
        }
        try {
            const res = await fetch('/api/connectors/exchange_code', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': `Bearer ${jwt}`
                },
                body: JSON.stringify({
                    auth_code: authCode,
                    redirect_uri: window.location.origin,
                    service_id: stored.service
                })
            });
            if (res.status === 401) {
                clearStoredAccountFields();
                showToast('Your session expired during auth. Please log in again.', true);
                setTimeout(() => window.location.href = '/static/login.html', 1500);
                return;
            }
            const data = await res.json();
            if (!res.ok || data.status !== 'success') {
                throw new Error(data.detail || data.message || 'Connector Auth Failed');
            }
            const checkbox = document.getElementById(`checkbox-${stored.service}`);
            if (checkbox) checkbox.checked = true;
            fetchConnectorsStatus();
            showToast('Google Workspace connected.');
        } catch (err) {
            const checkbox = document.getElementById(`checkbox-${stored.service}`);
            if (checkbox) checkbox.checked = false;
            showToast(err.message || 'Connector Network Error', true);
        }
    }
}

function startGoogleAccessTokenRedirect(purpose, scope = 'openid email profile') {
    const state = createGoogleOAuthState(purpose);
    storeGoogleFullscreenAuth({
        state,
        purpose,
        language: getPreferredLanguage(),
        returnPath: '/'
    });
    const params = new URLSearchParams({
        client_id: googleOAuthClientId,
        redirect_uri: googleLoginRedirectUri(),
        response_type: 'token',
        scope,
        include_granted_scopes: 'true',
        prompt: 'select_account',
        state
    });
    window.location.assign(`https://accounts.google.com/o/oauth2/v2/auth?${params.toString()}`);
}

handleGoogleFullscreenReturn().catch(err => {
    console.warn('Google fullscreen return handling failed', err);
    showToast('Google authorization failed. Please try again.', true);
});

function updateGuestInputUi() {
    const shouldLockGuestAgent = !currentUserId && isAgentMode;
    document.body.classList.toggle('is-guest', !currentUserId);
    document.body.classList.toggle('is-logged-in', !!currentUserId);
    syncSidebarOpenAvailability();

    if (inputWrapper) {
        inputWrapper.classList.remove('guest-input-hidden');
    }
    if (appContainer) {
        appContainer.classList.remove('guest-input-hidden');
    }
    if (composerInput) {
        composerInput.classList.toggle('guest-agent-locked', shouldLockGuestAgent);
    }
    if (userInput) {
        userInput.readOnly = shouldLockGuestAgent;
        if (shouldLockGuestAgent) {
            userInput.blur();
        }
    }
}

function pulseGuestLoginPrompt() {
    if (currentUserId || !guestLimitBanner) return;
    showGuestLoginPrompt(true);
    const now = Date.now();
    if (now - lastGuestPromptPulseAt < 220) return;
    lastGuestPromptPulseAt = now;

    if (composerInput) {
        composerInput.classList.remove('lock-tap');
        void composerInput.offsetWidth;
        composerInput.classList.add('lock-tap');
        composerInput.addEventListener('animationend', () => {
            composerInput.classList.remove('lock-tap');
        }, { once: true });
    }

    guestLimitBanner.classList.remove('attention');
    void guestLimitBanner.offsetWidth;
    guestLimitBanner.classList.add('attention');
    guestLimitBanner.addEventListener('animationend', () => {
        guestLimitBanner.classList.remove('attention');
    }, { once: true });
}

function showGuestLoginPrompt(force = false) {
    if (currentUserId || !guestLimitBanner) return;
    if (force) guestLoginPromptForced = true;
    updateGuestLimitBannerCopy();
    guestLimitBanner.hidden = false;
    guestLimitBanner.classList.add('show');
    if (appContainer) appContainer.classList.add('guest-cta-visible');
    updateGuestInputUi();
}

function hideGuestLoginPrompt(resetForce = false) {
    if (!guestLimitBanner) return;
    if (resetForce) guestLoginPromptForced = false;
    guestLimitBanner.classList.remove('show');
    guestLimitBanner.hidden = true;
    if (appContainer) appContainer.classList.remove('guest-cta-visible');
    updateGuestInputUi();
}

function showGuestQuestionAuthModal() {
    if (currentUserId) return;
    if (!guestAuthRequiredModal) {
        showGuestLoginPrompt(true);
        return;
    }
    guestAuthReturnFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    guestAuthRequiredModal.hidden = false;
    guestAuthRequiredModal.setAttribute('aria-hidden', 'false');
    requestAnimationFrame(() => {
        guestAuthRequiredModal.classList.add('show');
        closeGuestAuthModalBtn?.focus();
    });
}

function closeGuestQuestionAuthModal({ restoreFocus = true } = {}) {
    if (!guestAuthRequiredModal || guestAuthRequiredModal.hidden) return;
    guestAuthRequiredModal.classList.remove('show');
    guestAuthRequiredModal.setAttribute('aria-hidden', 'true');
    window.setTimeout(() => {
        if (!guestAuthRequiredModal.classList.contains('show')) {
            guestAuthRequiredModal.hidden = true;
            if (restoreFocus) guestAuthReturnFocus?.focus?.();
            guestAuthReturnFocus = null;
        }
    }, 220);
}

function syncGuestAccessState() {
    currentUserId = localStorage.getItem('pepperUserId') || null;
    currentUsername = localStorage.getItem('pepperUsername') || null;
    updateGuestLimitBannerCopy();
    if (agentModeBtn) agentModeBtn.classList.toggle('requires-login', !currentUserId);

    if (currentUserId) {
        hideGuestLoginPrompt(true);
        return;
    }

    // The old two-question visitor allowance is retired. Visitors may type so
    // they can see the sign-in prompt on send, but cannot start a chat.
    localStorage.removeItem('pepperGuestQuestionCount');
    hideGuestLoginPrompt(true);
    updateGuestInputUi();
}

if (guestLimitLoginBtn) {
    guestLimitLoginBtn.addEventListener('click', () => {
        window.location.href = '/static/login.html';
    });
}

if (guestLimitRegisterBtn) {
    guestLimitRegisterBtn.addEventListener('click', () => {
        window.location.href = '/static/login.html?mode=register';
    });
}

if (closeGuestAuthModalBtn) {
    closeGuestAuthModalBtn.addEventListener('click', () => closeGuestQuestionAuthModal());
}

if (guestAuthRequiredModal) {
    guestAuthRequiredModal.addEventListener('click', event => {
        if (event.target === guestAuthRequiredModal) closeGuestQuestionAuthModal();
    });
}

document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && guestAuthRequiredModal && !guestAuthRequiredModal.hidden) {
        event.preventDefault();
        closeGuestQuestionAuthModal();
    }
});

if (currentUsername) {
    const ud = document.getElementById('userDisplay');
    if (ud) ud.innerText = currentUsername;
}

// Global Event Delegation for dynamic UI elements (like Gmail Preview Toggle)
document.body.addEventListener('click', function(e) {
    const btn = e.target.closest('.gmail-preview-toggle-btn');
    if (btn) {
        var c = btn.closest('.gmail-preview-container').querySelector('.gmail-preview-body');
        if(c && c.style.maxHeight){
            c.style.maxHeight='';
            c.style.webkitMaskImage='none';
            c.style.maskImage='none';
            btn.innerHTML='<i class="fa-solid fa-chevron-up"></i> Collapse Preview';
        } else {
            c.style.maxHeight='180px';
            c.style.webkitMaskImage='linear-gradient(to bottom, black 50%, transparent 100%)';
            c.style.maskImage='linear-gradient(to bottom, black 50%, transparent 100%)';
            btn.innerHTML='<i class="fa-solid fa-chevron-down"></i> Expand Preview';
        }
    }
});

// ============ Sidebar Toggle ============
const sidebarCloseBtn = document.getElementById('sidebarCloseBtn');
const sidebarOpenBtn = document.getElementById('sidebarOpenBtn');
const sidebarExpandBtn = document.getElementById('sidebarExpandBtn');
const pageWrapper = document.querySelector('.page-wrapper');

function isGuestSidebarDisabled() {
    return isMobileLayout() && document.body.classList.contains('is-guest');
}

function syncMobileSidebarBodyState() {
    if (!pageWrapper) return;
    const isOpen = isMobileLayout()
        && !document.body.classList.contains('is-guest')
        && !pageWrapper.classList.contains('sidebar-collapsed');
    document.body.classList.toggle('mobile-sidebar-open', isOpen);
    sidebarOpenBtn?.setAttribute('aria-expanded', isOpen ? 'true' : 'false');
}

function syncSidebarOpenAvailability() {
    if (!sidebarOpenBtn) return;
    const isGuest = !currentUserId || document.body.classList.contains('is-guest');
    sidebarOpenBtn.disabled = isGuest;
    sidebarOpenBtn.setAttribute('aria-hidden', isGuest ? 'true' : 'false');
    sidebarOpenBtn.tabIndex = isGuest ? -1 : 0;
}

function setSidebarCollapsed(collapsed, { skipHistory = false } = {}) {
    if (!pageWrapper) return;
    const wasCollapsed = pageWrapper.classList.contains('sidebar-collapsed');
    if (!collapsed && isGuestSidebarDisabled()) {
        pageWrapper.classList.add('sidebar-collapsed');
        document.body.classList.remove('mobile-sidebar-open');
        sidebarOpenBtn?.setAttribute('aria-expanded', 'false');
        return;
    }
    pageWrapper.classList.toggle('sidebar-collapsed', collapsed);
    syncMobileSidebarBodyState();

    // On phones, opening the drawer should immediately reveal a fresh,
    // expanded history list. This avoids a hidden/collapsed list being mistaken
    // for an empty history while an earlier request is still in flight.
    if (!collapsed && wasCollapsed && isMobileLayout()) {
        document.body.classList.remove('mobile-header-hidden');
        const mobileHistoryList = document.getElementById('historyList');
        const mobileHistoryToggle = document.getElementById('historyToggleBtn');
        const mobileSidebarContent = document.querySelector('.sidebar-content');
        mobileHistoryList?.classList.remove('collapsed');
        mobileHistoryToggle?.classList.remove('collapsed');
        mobileSidebarContent?.classList.remove('history-collapsed');
        window.requestAnimationFrame(() => {
            loadHistory();
        });
    }

    if (isMobileLayout() && wasCollapsed !== collapsed) {
        if (collapsed) {
            if (!skipHistory) markMobileHistoryOverlayClosed('sidebar');
        } else {
            markMobileHistoryOverlayOpen('sidebar');
        }
    }
}

registerMobileHistoryOverlay('sidebar', () => setSidebarCollapsed(true, { skipHistory: true }));

let previousMobileSidebarLayout = null;
function syncMobileSidebarState() {
    if (!pageWrapper) return;
    const mobile = isMobileLayout();
    if (previousMobileSidebarLayout === mobile) {
        syncMobileSidebarBodyState();
        syncSidebarOpenAvailability();
        return;
    }

    if (mobile) {
        setSidebarCollapsed(true, { skipHistory: true });
    } else {
        if (previousMobileSidebarLayout === true) {
            markMobileHistoryOverlayClosed('sidebar');
        }
        syncMobileSidebarBodyState();
    }
    previousMobileSidebarLayout = mobile;
    syncSidebarOpenAvailability();
}

syncMobileSidebarState();
window.addEventListener('resize', syncMobileSidebarState);

if (sidebarCloseBtn) sidebarCloseBtn.addEventListener('click', () => setSidebarCollapsed(true));
if (sidebarOpenBtn) sidebarOpenBtn.addEventListener('click', () => setSidebarCollapsed(false));
if (sidebarExpandBtn) sidebarExpandBtn.addEventListener('click', () => setSidebarCollapsed(false));

document.querySelectorAll('#newChatBtn, #openSearchModalBtn, #agentModeBtn').forEach((btn) => {
    btn.addEventListener('click', () => {
        if (isMobileLayout()) {
            setTimeout(() => setSidebarCollapsed(true), 0);
        }
    });
});

document.addEventListener('click', (event) => {
    if (!isMobileLayout() || !pageWrapper || pageWrapper.classList.contains('sidebar-collapsed')) return;
    // Native dialogs render in the browser top layer, outside the sidebar's
    // DOM subtree. Interacting with a confirmation or mobile picker must not
    // be mistaken for tapping the page backdrop and collapse the drawer.
    if (event.target.closest('dialog[open], .modal-overlay.show, .account-page-overlay.show, .pdf-preview-overlay.show')) return;
    const sidebar = document.getElementById('sidebar');
    if (sidebar?.contains(event.target) || sidebarOpenBtn?.contains(event.target)) return;
    setSidebarCollapsed(true);
});

// The fixed mobile header should never cover the conversation being read.
// Hide it only after a deliberate downward scroll; reveal it as soon as the
// reader scrolls upward, reaches the top, opens the drawer, or focuses input.
const mobileHeaderElement = document.getElementById('mobileHeader');
const mobileChatArea = document.getElementById('chatArea');
let mobileHeaderLastScrollTop = 0;
let mobileHeaderScrollFrame = 0;

function setMobileHeaderHidden(hidden) {
    if (!isMobileLayout() || document.body.classList.contains('mobile-sidebar-open')) {
        document.body.classList.remove('mobile-header-hidden');
        return;
    }
    document.body.classList.toggle('mobile-header-hidden', Boolean(hidden));
}

function syncMobileHeaderOnChatScroll() {
    mobileHeaderScrollFrame = 0;
    if (!mobileChatArea || !isMobileLayout() || document.body.classList.contains('is-guest')) return;
    const nextScrollTop = Math.max(0, mobileChatArea.scrollTop);
    const delta = nextScrollTop - mobileHeaderLastScrollTop;
    if (nextScrollTop < 10) {
        setMobileHeaderHidden(false);
    } else if (delta > 8) {
        setMobileHeaderHidden(true);
    } else if (delta < -6) {
        setMobileHeaderHidden(false);
    }
    mobileHeaderLastScrollTop = nextScrollTop;
}

if (mobileChatArea && mobileHeaderElement) {
    mobileChatArea.addEventListener('scroll', () => {
        if (!mobileHeaderScrollFrame) {
            mobileHeaderScrollFrame = window.requestAnimationFrame(syncMobileHeaderOnChatScroll);
        }
    }, { passive: true });
}
document.getElementById('userInput')?.addEventListener('focus', () => setMobileHeaderHidden(false));
window.addEventListener('resize', () => {
    if (!isMobileLayout()) document.body.classList.remove('mobile-header-hidden');
});

// ============ History ============
const historyToggleBtn = document.getElementById('historyToggleBtn');
const historyList = document.getElementById('historyList');
const sidebarContent = document.querySelector('.sidebar-content');

if (historyToggleBtn && historyList) {
    const syncHistoryCollapsedState = () => {
        if (sidebarContent) {
            sidebarContent.classList.toggle('history-collapsed', historyList.classList.contains('collapsed'));
        }
    };
    syncHistoryCollapsedState();

    historyToggleBtn.addEventListener('click', () => {
        historyToggleBtn.classList.toggle('collapsed');
        historyList.classList.toggle('collapsed');
        syncHistoryCollapsedState();
    });
}

function historyChatUrl(chatId) {
    return `/api/history/${encodeURIComponent(chatId)}`;
}

function setActiveHistoryChat(chatId) {
    document.querySelectorAll('.history-item[data-chat-id]').forEach(item => {
        item.classList.toggle('active', item.dataset.chatId === chatId);
    });
}

function openHistoryChat(chatId, options = {}) {
    if (isGenerating || !chatId) return;
    currentChatId = chatId;
    setActiveHistoryChat(chatId);
    if (isMobileLayout()) {
        setSidebarCollapsed(true);
    }
    if (options.closeModal) {
        closeSearchHistoryModal();
    }
    loadChat(chatId);
}

function animateHistoryItemRemoval(item) {
    return new Promise(resolve => {
        if (!item) {
            resolve();
            return;
        }
        let done = false;
        const finish = () => {
            if (done) return;
            done = true;
            item.remove();
            resolve();
        };
        item.classList.remove('is-pending-delete');
        item.classList.add('is-deleting');
        item.addEventListener('animationend', finish, { once: true });
        setTimeout(finish, 260);
    });
}

const historyDeleteDialog = document.getElementById('historyDeleteDialog');
const historyDeleteCancelBtn = document.getElementById('historyDeleteCancelBtn');
const historyDeleteConfirmBtn = document.getElementById('historyDeleteConfirmBtn');
let historyDeleteDialogResolver = null;

function refreshHistoryDeleteDialogCopy() {
    const title = tUi('historyDeleteTitle', 'Delete conversation?');
    const description = tUi('historyDeleteDescription', 'This conversation will be permanently deleted.');
    const cancel = tUi('historyDeleteCancel', 'Cancel');
    const confirm = tUi('historyDeleteConfirm', 'Delete');
    const titleEl = document.getElementById('historyDeleteDialogTitle');
    const descriptionEl = document.getElementById('historyDeleteDialogDescription');
    if (titleEl) titleEl.textContent = title;
    if (descriptionEl) descriptionEl.textContent = description;
    if (historyDeleteCancelBtn) {
        historyDeleteCancelBtn.textContent = cancel;
        historyDeleteCancelBtn.setAttribute('aria-label', cancel);
    }
    if (historyDeleteConfirmBtn) {
        historyDeleteConfirmBtn.textContent = confirm;
        historyDeleteConfirmBtn.setAttribute('aria-label', confirm);
    }
}

function confirmHistoryDeletion() {
    if (!historyDeleteDialog || typeof historyDeleteDialog.showModal !== 'function' || historyDeleteDialog.open) {
        return Promise.resolve(false);
    }
    refreshHistoryDeleteDialogCopy();
    historyDeleteDialog.returnValue = '';
    historyDeleteDialog.showModal();
    window.setTimeout(() => historyDeleteCancelBtn?.focus({ preventScroll: true }), 0);
    return new Promise(resolve => {
        historyDeleteDialogResolver = resolve;
    });
}

if (historyDeleteDialog) {
    historyDeleteDialog.addEventListener('close', () => {
        const resolver = historyDeleteDialogResolver;
        historyDeleteDialogResolver = null;
        resolver?.(historyDeleteDialog.returnValue === 'confirm');
    });
    historyDeleteDialog.addEventListener('click', event => {
        if (event.target === historyDeleteDialog) historyDeleteDialog.close('cancel');
    });
}

async function deleteHistoryChat(chatId, sourceElement, options = {}) {
    if (!chatId) return false;
    if (!(await confirmHistoryDeletion())) return false;

    const item = sourceElement?.closest?.('.history-item') || sourceElement;
    if (item?.classList.contains('is-pending-delete') || item?.classList.contains('is-deleting')) return false;

    item?.classList.add('is-pending-delete');
    try {
        const res = await fetch(historyChatUrl(chatId), {
            method: 'DELETE',
            headers: authenticatedHeaders(),
        });
        if (!res.ok) throw new Error(`Delete failed with status ${res.status}`);

        await animateHistoryItemRemoval(item);
        if (currentChatId === chatId) {
            document.getElementById('newChatBtn')?.click();
        }
        invalidateRecentFilesCache();
        loadHistory();
        if (options.clearPreview && typeof resetSearchPreview === 'function') {
            resetSearchPreview();
        }
        return true;
    } catch (err) {
        console.error("Failed to delete", err);
        item?.classList.remove('is-pending-delete');
        return false;
    }
}

async function loadHistory() {
    if (!currentUserId) {
        const hl = document.getElementById('historyList');
        if(hl) hl.innerHTML = '<li class="history-placeholder">Please login to see history</li>';
        return;
    }
    try {
        const res = await fetch('/api/history', { headers: authenticatedHeaders() });
        const data = await res.json();
        const hl = document.getElementById('historyList');
        hl.innerHTML = '';
        if(data.chats && data.chats.length > 0) {
            const groups = {};
            const today = new Date();
            today.setHours(0,0,0,0);
            const yesterday = new Date(today);
            yesterday.setDate(yesterday.getDate() - 1);

            data.chats.forEach(chat => {
                const date = chat.updated_at ? new Date(chat.updated_at) : new Date();
                const chatDate = new Date(date);
                chatDate.setHours(0,0,0,0);

                let groupName = "";
                if (chatDate.getTime() === today.getTime()) {
                    groupName = "Today";
                } else if (chatDate.getTime() === yesterday.getTime()) {
                    groupName = "Yesterday";
                } else {
                    const diffTime = Math.abs(today - chatDate);
                    const diffDays = Math.ceil(diffTime / (1000 * 60 * 60 * 24));
                    if (diffDays <= 7) {
                        groupName = "Previous 7 Days";
                    } else if (diffDays <= 30) {
                        groupName = "Previous 30 Days";
                    } else {
                        const year = date.getFullYear();
                        const month = String(date.getMonth() + 1).padStart(2, '0');
                        groupName = `${year}-${month}`;
                    }
                }

                if (!groups[groupName]) groups[groupName] = [];
                groups[groupName].push(chat);
            });

            for (const [groupName, chats] of Object.entries(groups)) {
                const titleLi = document.createElement('li');
                titleLi.className = 'history-group-title';
                titleLi.innerText = groupName;
                hl.appendChild(titleLi);

                chats.forEach(chat => {
                    const li = document.createElement('li');
                    li.className = 'history-item';
                    if (chat._id === currentChatId) li.classList.add('active');
                    li.dataset.chatId = chat._id;

                    const textSpan = document.createElement('span');
                    textSpan.className = 'history-item-text';
                    textSpan.innerText = chat.title || "New Chat";

                    const delBtn = document.createElement('button');
                    delBtn.className = 'history-del-btn';
                    delBtn.type = 'button';
                    delBtn.innerHTML = '<i class="fa-regular fa-trash-can"></i>';
                    const deleteLabel = tUi('historyDeleteAction', 'Delete conversation');
                    delBtn.title = deleteLabel;
                    delBtn.setAttribute('aria-label', deleteLabel);
                    delBtn.dataset.i18nTitle = 'historyDeleteAction';
                    delBtn.dataset.i18nAriaLabel = 'historyDeleteAction';
                    delBtn.onclick = async (e) => {
                        e.stopPropagation();
                        deleteHistoryChat(chat._id, li);
                    };

                    li.onclick = () => {
                        openHistoryChat(chat._id);
                    };
                    li.appendChild(textSpan);
                    li.appendChild(delBtn);
                    hl.appendChild(li);
                });
            }
        } else {
            hl.innerHTML = '<li class="history-placeholder">No history yet</li>';
        }
    } catch(e) { console.error("Failed to load history", e); }
}

let activeHistoryLoadId = 0;

function renderHistoryChatLoading() {
    const appContainer = document.querySelector('.app-container');
    if (!messagesContainer || !appContainer) return;
    logoContainer.style.display = 'none';
    appContainer.classList.remove('centered-landing');
    isFirstMessage = false;
    messagesContainer.innerHTML = `
        <div class="history-chat-loading" role="status" aria-live="polite">
            <span class="history-chat-loading__dot"></span>
            <span class="history-chat-loading__dot"></span>
            <span class="history-chat-loading__dot"></span>
            <span class="history-chat-loading__label">${escapeHtml(tUi('historyOpening', 'Opening conversation…'))}</span>
        </div>
    `;
    scrollChatToTop({ smooth: false });
}

function renderHistoryChatLoadError() {
    if (!messagesContainer) return;
    messagesContainer.innerHTML = `
        <div class="history-chat-load-error" role="alert">${escapeHtml(tUi('historyOpenFailed', 'We could not open this conversation. Please try again.'))}</div>
    `;
}

async function loadChat(chatId) {
    if (isGenerating) return;
    const requestId = ++activeHistoryLoadId;
    renderHistoryChatLoading();
    try {
        const res = await fetch(historyChatUrl(chatId), { headers: authenticatedHeaders() });
        if (!res.ok) throw new Error(`History request failed with status ${res.status}`);
        const data = await res.json();
        if (requestId !== activeHistoryLoadId) return;
        if(data.chat) {
            currentChatId = chatId;
            messagesContainer.innerHTML = '';
            logoContainer.style.display = 'none';
            document.querySelector('.app-container').classList.remove('centered-landing');
            isFirstMessage = false;

            // Restore agent mode / normal mode based on saved flag
            const wasAgentMode = !!data.chat.agent_mode;
            isAgentMode = wasAgentMode;
            clearSelectedSkillType();

            document.querySelectorAll('.nav-menu-btn').forEach(btn => btn.classList.remove('active'));

            if (wasAgentMode) {
                document.getElementById('agentModeBtn').classList.add('active');
            } else {
                document.getElementById('newChatBtn').classList.add('active');
            }
            syncComposerModeControls();

            chatMessages = data.chat.messages || [];
            let feedbacks = data.chat.feedback || {};
            chatMessages.forEach((msg, idx) => {
                appendMessage(msg.content, msg.role, msg, idx, feedbacks[idx.toString()] || 0, true);
            });
            setTimeout(() => scrollChatToTop({ smooth: true }), 50);
        } else {
            throw new Error('History response did not include a chat');
        }
    } catch (e) {
        if (requestId !== activeHistoryLoadId) return;
        console.error("Failed to load chat", e);
        renderHistoryChatLoadError();
    }
}

function openFreshNormalChat() {
    isAgentMode = false;
    clearSelectedSkillType();
    syncComposerModeControls();
    resizeComposer();
    currentChatId = null;
    chatMessages = [];
    messagesContainer.innerHTML = '';
    logoContainer.style.display = 'flex';
    logoContainer.style.opacity = '1';
    document.querySelector('.app-container').classList.add('centered-landing');
    isFirstMessage = true;
    logoContainer.innerHTML = getNormalLandingMarkup();
    document.querySelectorAll('.nav-menu-btn').forEach(btn => btn.classList.remove('active'));
    document.getElementById('newChatBtn').classList.add('active');
    if (!currentUserId) {
        guestLoginPromptForced = false;
    }
    syncGuestAccessState();
}

function openFreshAgentChat(showLoginPrompt = false) {
    isAgentMode = true;
    clearSelectedSkillType();
    syncComposerModeControls();
    resizeComposer();
    currentChatId = null;
    chatMessages = [];
    messagesContainer.innerHTML = '';
    logoContainer.style.display = 'flex';
    logoContainer.style.opacity = '1';
    document.querySelector('.app-container').classList.add('centered-landing');
    isFirstMessage = true;
    logoContainer.innerHTML = '<h2>Your personalize AI agent</h2>';
    document.querySelectorAll('.nav-menu-btn').forEach(btn => btn.classList.remove('active'));
    document.getElementById('agentModeBtn').classList.add('active');
    if (showLoginPrompt) {
        showGuestLoginPrompt(true);
    } else {
        updateGuestInputUi();
    }
}

document.getElementById('newChatBtn').addEventListener('click', () => {
    if (isGenerating) return;
    openFreshNormalChat();
});

document.getElementById('agentModeBtn').addEventListener('click', () => {
    if (isGenerating) return;
    if (!currentUserId) {
        openFreshAgentChat(true);
        return;
    }
    openFreshAgentChat(false);
});
loadHistory();
syncGuestAccessState();

if (composerInput) {
    const lockedGuestAgentHandler = (e) => {
        if (!currentUserId && isAgentMode) {
            e.preventDefault();
            e.stopPropagation();
            pulseGuestLoginPrompt();
        }
    };
    composerInput.addEventListener('pointerdown', lockedGuestAgentHandler, true);
    composerInput.addEventListener('focusin', lockedGuestAgentHandler, true);
}

// ============ Toggles ============
function updateTogglesUI() {
    if (thinkToggle) thinkToggle.classList.toggle('active', supportsThinkMode && isThinkMode);
}
updateTogglesUI();
applyThinkModeAvailability();
thinkToggle.addEventListener('click', () => {
    if (!supportsThinkMode) return;
    isThinkMode = !isThinkMode;
    updateTogglesUI();
});
const searchModeSelect = document.getElementById('searchModeSelect');
const modeSelectShell = document.getElementById('modeSelectShell');
const modeSelectTrigger = document.getElementById('modeSelectTrigger');
const modeSelectLabel = document.getElementById('modeSelectLabel');
const modeDropdown = document.getElementById('modeDropdown');
const mobileModeSheet = document.getElementById('mobileModeSheet');
const mobileModeSheetCloseBtn = document.getElementById('mobileModeSheetCloseBtn');
let mobileModeSheetReturnFocus = null;

function isMobileModeSheetOpen() {
    return Boolean(mobileModeSheet?.open);
}

function closeMobileModeSheet({ restoreFocus = false } = {}) {
    if (!isMobileModeSheetOpen()) return;
    mobileModeSheet.close();
    if (restoreFocus) {
        window.setTimeout(() => {
            (mobileModeSheetReturnFocus || modeSelectTrigger)?.focus?.({ preventScroll: true });
        }, 0);
    }
}

function openMobileModeSheet() {
    if (!mobileModeSheet || !isMobileModePickerLayout() || isMobileModeSheetOpen()) return;
    mobileModeSheetReturnFocus = document.activeElement instanceof HTMLElement
        ? document.activeElement
        : modeSelectTrigger;
    // Opening a picker should first dismiss the virtual keyboard. This gives
    // the sheet its full dynamic viewport instead of hiding options behind it.
    if (document.activeElement instanceof HTMLElement) document.activeElement.blur();
    modeSelectShell?.classList.remove('open');
    modeSelectTrigger?.setAttribute('aria-expanded', 'true');
    document.body.classList.add('mobile-mode-sheet-open');
    mobileModeSheet.showModal();
    window.setTimeout(() => {
        mobileModeSheetCloseBtn?.focus({ preventScroll: true });
    }, 0);
}

function setModeDropdownOpen(open) {
    if (!open) closeMobileModeSheet();
    if (!modeSelectShell || !modeSelectTrigger) return;
    modeSelectShell.classList.toggle('open', open);
    modeSelectTrigger.setAttribute('aria-expanded', open ? 'true' : 'false');
}

function selectSearchMode(mode) {
    if (!searchModeSelect) return;
    searchModeSelect.value = mode;
    searchModeSelect.dispatchEvent(new Event('change'));
}

function syncSearchModeUi() {
    if (!searchModeSelect) return;
    const value = searchModeSelect.value || 'normal';
    isWebMode = value === 'web';
    if (modeSelectLabel) {
        modeSelectLabel.textContent = isWebMode
            ? tUi('modeWeb', 'Web')
            : tUi('modeFast', 'Fast');
    }
    if (modeDropdown) {
        modeDropdown.querySelectorAll('[data-mode]').forEach(option => {
            const selected = option.dataset.mode === value;
            option.classList.toggle('is-selected', selected);
            option.setAttribute('aria-selected', selected ? 'true' : 'false');
            const mark = option.querySelector('.mode-option-mark');
            if (!mark) return;
            if (selected) {
                mark.innerHTML = '<i class="fa-solid fa-check"></i>';
            } else {
                mark.innerHTML = '';
            }
        });
    }
    if (mobileModeSheet) {
        mobileModeSheet.querySelectorAll('[data-mobile-mode]').forEach(option => {
            const selected = option.dataset.mobileMode === value;
            option.classList.toggle('is-selected', selected);
            option.setAttribute('aria-selected', selected ? 'true' : 'false');
        });
    }
}

if (searchModeSelect) {
    searchModeSelect.addEventListener('change', syncSearchModeUi);
    syncSearchModeUi();
}

if (modeSelectShell && modeSelectTrigger && modeDropdown) {
    modeSelectTrigger.addEventListener('click', (e) => {
        e.stopPropagation();
        if (connectorsContainer) connectorsContainer.classList.remove('open');
        setUploadMenuOpen(false);
        if (isMobileModePickerLayout()) {
            if (isMobileModeSheetOpen()) closeMobileModeSheet({ restoreFocus: true });
            else openMobileModeSheet();
            return;
        }
        setModeDropdownOpen(!modeSelectShell.classList.contains('open'));
    });

    modeDropdown.querySelectorAll('[data-mode]').forEach(option => {
        option.addEventListener('click', (e) => {
            e.stopPropagation();
            if (option.disabled || !searchModeSelect) return;
            selectSearchMode(option.dataset.mode);
            setModeDropdownOpen(false);
        });
    });

    document.addEventListener('click', (e) => {
        if (!modeSelectShell.contains(e.target)) setModeDropdownOpen(false);
    });

    document.addEventListener('keydown', (e) => {
        if (e.key !== 'Escape') return;
        if (isMobileModeSheetOpen()) {
            e.preventDefault();
            closeMobileModeSheet({ restoreFocus: true });
            return;
        }
        setModeDropdownOpen(false);
    });
}

if (mobileModeSheet) {
    mobileModeSheet.querySelectorAll('[data-mobile-mode]').forEach(option => {
        option.addEventListener('click', () => {
            selectSearchMode(option.dataset.mobileMode);
            closeMobileModeSheet({ restoreFocus: true });
        });
    });
    mobileModeSheetCloseBtn?.addEventListener('click', () => closeMobileModeSheet({ restoreFocus: true }));
    mobileModeSheet.addEventListener('click', event => {
        if (event.target === mobileModeSheet) closeMobileModeSheet({ restoreFocus: true });
    });
    // Native dialogs close themselves for Escape. Restore the trigger focus as
    // well, so keyboard users return to the same control they opened.
    mobileModeSheet.addEventListener('cancel', () => {
        window.setTimeout(() => {
            (mobileModeSheetReturnFocus || modeSelectTrigger)?.focus?.({ preventScroll: true });
        }, 0);
    });
    mobileModeSheet.addEventListener('close', () => {
        document.body.classList.remove('mobile-mode-sheet-open');
        modeSelectTrigger?.setAttribute('aria-expanded', 'false');
    });
}

window.addEventListener('resize', () => {
    if (!isMobileModePickerLayout()) closeMobileModeSheet();
});
window.addEventListener('mof-preferences-changed', syncSearchModeUi);

// ============ Connectors ============
const connectorBtn = document.getElementById('connectorBtn');
const connectorsContainer = document.getElementById('connectorsContainer');
const connectorDropdown = document.getElementById('connectorDropdown');
const mobileConnectorSheet = document.getElementById('mobileConnectorSheet');
const mobileConnectorSheetList = document.getElementById('mobileConnectorSheetList');
const mobileConnectorSheetCloseBtn = document.getElementById('mobileConnectorSheetCloseBtn');
let mobileConnectorSheetReturnFocus = null;

function isMobileConnectorSheetOpen() {
    return Boolean(mobileConnectorSheet?.open);
}

function moveConnectorControlsToMobileSheet() {
    if (!connectorDropdown || !mobileConnectorSheetList) return;
    while (connectorDropdown.firstChild) {
        mobileConnectorSheetList.appendChild(connectorDropdown.firstChild);
    }
}

function restoreConnectorControlsFromMobileSheet() {
    if (!connectorDropdown || !mobileConnectorSheetList) return;
    while (mobileConnectorSheetList.firstChild) {
        connectorDropdown.appendChild(mobileConnectorSheetList.firstChild);
    }
}

function closeMobileConnectorSheet({ restoreFocus = false } = {}) {
    if (!isMobileConnectorSheetOpen()) return;
    mobileConnectorSheet.close();
    if (restoreFocus) {
        window.setTimeout(() => {
            (mobileConnectorSheetReturnFocus || connectorBtn)?.focus?.({ preventScroll: true });
        }, 0);
    }
}

function openMobileConnectorSheet() {
    if (!mobileConnectorSheet || !isMobileModePickerLayout() || isMobileConnectorSheetOpen()) return;
    mobileConnectorSheetReturnFocus = document.activeElement instanceof HTMLElement
        ? document.activeElement
        : connectorBtn;
    if (document.activeElement instanceof HTMLElement) document.activeElement.blur();
    setModeDropdownOpen(false);
    setUploadMenuOpen(false);
    connectorsContainer?.classList.remove('open');
    moveConnectorControlsToMobileSheet();
    connectorBtn?.setAttribute('aria-expanded', 'true');
    document.body.classList.add('mobile-connector-sheet-open');
    mobileConnectorSheet.showModal();
    fetchConnectorsStatus();
    window.setTimeout(() => mobileConnectorSheetCloseBtn?.focus({ preventScroll: true }), 0);
}

if (connectorBtn && connectorsContainer) {
    connectorBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        setModeDropdownOpen(false);
        setUploadMenuOpen(false);
        if (isMobileModePickerLayout()) {
            if (isMobileConnectorSheetOpen()) closeMobileConnectorSheet({ restoreFocus: true });
            else openMobileConnectorSheet();
            return;
        }
        connectorsContainer.classList.toggle('open');
    });

    document.addEventListener('click', (e) => {
        if (mobileConnectorSheet?.contains(e.target)) return;
        if (!connectorsContainer.contains(e.target)) {
            connectorsContainer.classList.remove('open');
        }
    });
}

if (mobileConnectorSheet) {
    mobileConnectorSheetCloseBtn?.addEventListener('click', () => closeMobileConnectorSheet({ restoreFocus: true }));
    mobileConnectorSheet.addEventListener('click', event => {
        if (event.target === mobileConnectorSheet) closeMobileConnectorSheet({ restoreFocus: true });
    });
    mobileConnectorSheet.addEventListener('cancel', () => {
        window.setTimeout(() => (mobileConnectorSheetReturnFocus || connectorBtn)?.focus?.({ preventScroll: true }), 0);
    });
    mobileConnectorSheet.addEventListener('close', () => {
        restoreConnectorControlsFromMobileSheet();
        document.body.classList.remove('mobile-connector-sheet-open');
        connectorBtn?.setAttribute('aria-expanded', 'false');
    });
}

window.addEventListener('resize', () => {
    if (!isMobileModePickerLayout()) closeMobileConnectorSheet();
});

function updateConnectorStatus(service, data) {
    const switchLabel = document.getElementById(`switch-${service}`);
    const checkbox = document.getElementById(`checkbox-${service}`);
    if (switchLabel && checkbox && data) {
        checkbox.checked = !!data.active;
    }
}

function clearConnectorChecks() {
    Object.keys(SCOPE_MAP).forEach(service => {
        updateConnectorStatus(service, { active: false });
    });
}

async function fetchConnectorsStatus() {
    const token = localStorage.getItem('pepperSession');
    if (!token) {
        clearConnectorChecks();
        return;
    }
    try {
        const res = await fetch('/api/connectors/status', {
            headers: { 'Authorization': `Bearer ${token}` }
        });
        if (!res.ok) {
            clearConnectorChecks();
            return;
        }
        const status = await res.json();
        Object.keys(SCOPE_MAP).forEach(service => {
            updateConnectorStatus(service, status[service] || { active: false });
        });
    } catch (err) {
        console.warn('Failed to fetch connector status', err);
    }
}

// Google OAuth client builder cache
const googleClients = {};
let pendingOAuthService = null;

function buildGoogleClient(service, oauthState = '') {
    if (typeof google === 'undefined' || !google.accounts || !google.accounts.oauth2) {
        return false;
    }
    if (!SCOPE_MAP[service]) {
        console.warn(`Unknown Google connector service: ${service}`);
        return false;
    }

    const loginHint = localStorage.getItem('pepperUsername');
    const config = {
        client_id: googleConnectorOAuthClientId,
        scope: SCOPE_MAP[service],
        include_granted_scopes: false,  // CRITICAL: Force strictly separate scope prompts
        ux_mode: 'redirect',
        redirect_uri: window.location.origin,
        state: oauthState,
        callback: async (response) => {
            if (response && response.code) {
                try {
                    const jwt = localStorage.getItem('pepperSession');
                    if (!jwt) {
                        showToast("Please login first.", true);
                        return;
                    }
                    const res = await fetch('/api/connectors/exchange_code', {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json',
                            'Authorization': `Bearer ${jwt}`
                        },
                        body: JSON.stringify({
                            auth_code: response.code,
                            redirect_uri: window.location.origin,
                            service_id: pendingOAuthService
                        })
                    });

                    if (res.status === 401) {
                        clearStoredAccountFields();
                        showToast("Your session expired during auth. Please log in again.", true);
                        setTimeout(() => window.location.href = '/static/login.html', 1500);
                        return;
                    }

                    const data = await res.json();
                    if (res.ok && data.status === "success") {
                        const checkbox = document.getElementById(`checkbox-${pendingOAuthService}`);
                        if (checkbox) checkbox.checked = true;
                        fetchConnectorsStatus();
                    } else {
                        showToast("Connector Auth Failed: " + (data.detail || data.message || "Unknown error"), true);
                        const checkbox = document.getElementById(`checkbox-${pendingOAuthService}`);
                        if (checkbox) checkbox.checked = false;
                    }
                } catch (e) {
                    showToast("Connector Network Error", true);
                }
            }
        }
    };

    if (loginHint && loginHint.includes('@')) {
        config.login_hint = loginHint;
    }

    try {
        googleClients[service] = google.accounts.oauth2.initCodeClient(config);
        return true;
    } catch (err) {
        console.warn(`Failed to initialize Google connector client for ${service}`, err);
        return false;
    }
}

function initGoogleClients() {
    if (typeof google === 'undefined') {
        setTimeout(initGoogleClients, 500);
        return;
    }
    Object.keys(SCOPE_MAP).forEach(service => {
        buildGoogleClient(service);
    });
}

const SCOPE_MAP = {
    'drive': 'https://www.googleapis.com/auth/drive.file',
    'gmail': 'https://www.googleapis.com/auth/gmail.send',
    'docs': 'https://www.googleapis.com/auth/documents',
    'sheets': 'https://www.googleapis.com/auth/spreadsheets https://www.googleapis.com/auth/drive.metadata.readonly',
    'slides': 'https://www.googleapis.com/auth/presentations https://www.googleapis.com/auth/drive.file',
    'calendar': 'https://www.googleapis.com/auth/calendar.events',
    'meet': 'https://www.googleapis.com/auth/calendar.events'
};

// When a switch is clicked, trigger Google OAuth with granular scope OR toggle state
document.querySelectorAll('.liquid-glass-switch').forEach(switchLabel => {
    switchLabel.addEventListener('click', async (e) => {
        // Stop bubbling and native checkbox toggling! We control this!
        e.preventDefault();
        e.stopPropagation();

        const token = localStorage.getItem('pepperSession');
        if (!token) {
            if (connectorsContainer) connectorsContainer.classList.remove('open');
            const authModal = document.getElementById('authRequiredModal');
            if (authModal) authModal.classList.add('show');
            return;
        }

        const service = switchLabel.dataset.service;
        const checkbox = document.getElementById(`checkbox-${service}`);

        const isCurrentlyActive = checkbox.checked;
        const willBeActive = !isCurrentlyActive;

        if (willBeActive) {
            // Block if Google account not linked
            const googleLinked = localStorage.getItem('pepperGoogleLinked') === 'true';
            if (!googleLinked) {
                const _lang = getPreferredLanguage();
                const _msgs = { zh: '请先在账户设置中绑定您的 Google 账号后再使用连接器。', en: 'Please link your Google account in Account Settings before using connectors.', ms: 'Sila pautkan akaun Google anda dalam Tetapan Akaun sebelum menggunakan penyambung.' };
                showToast(_msgs[_lang] || _msgs.en, true);
                return;
            }
            // Turning ON always opens a full-screen Google redirect to retrieve/confirm scope.
            await publicConfigReady;
            const oauthState = createGoogleOAuthState(`connector-${service}`);
            sessionStorage.setItem('pepperGoogleConnectorAuth', JSON.stringify({ state: oauthState, service }));
            buildGoogleClient(service, oauthState);
            if (!googleClients[service]) {
                sessionStorage.removeItem('pepperGoogleConnectorAuth');
                showToast("Google OAuth loading... please wait.", true);
                return;
            }
            if (connectorsContainer) connectorsContainer.classList.remove('open');
            pendingOAuthService = service;
            googleClients[service].requestCode();
        } else {
            // Turning it OFF. Send toggle request to remove the scope logically
            try {
                // Optimistic UI update which instantly triggers CSS gray color
                checkbox.checked = false;

                const res = await fetch('/api/connectors/toggle', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'Authorization': `Bearer ${token}`
                    },
                    body: JSON.stringify({ service: service, enabled: willBeActive })
                });
                if (!res.ok) throw new Error('Toggle failed');
                updateConnectorStatus(service, { active: false });
            } catch (err) {
                console.error("Toggle failed", err);
                // Revert UI on failure
                checkbox.checked = !willBeActive;
            }
        }
    });
});

// Init Connectors UI data and build background OAuth clients
setTimeout(async () => {
    await publicConfigReady;
    initGoogleClients();
    fetchConnectorsStatus();
}, 1200);

// ============ Helpers ============
let scrollToBottomFrame = null;

function getChatScrollTarget(chatArea) {
    const style = window.getComputedStyle(chatArea);
    const chatCanScroll = style.overflowY !== 'visible'
        && style.overflowY !== 'clip'
        && chatArea.scrollHeight > chatArea.clientHeight + 2;
    if (chatCanScroll) {
        return { element: chatArea, isWindow: false };
    }
    return { element: document.scrollingElement || document.documentElement, isWindow: true };
}

function scrollChatToTop(options = {}) {
    const chatArea = document.getElementById('chatArea');
    if (!chatArea) return;
    const target = getChatScrollTarget(chatArea);
    const scrollOptions = { top: 0, behavior: options.smooth ? 'smooth' : 'auto' };
    if (target.isWindow) {
        window.scrollTo(scrollOptions);
    } else {
        target.element.scrollTo(scrollOptions);
    }
}

function scrollToBottom(options = {}) {
    const chatArea = document.getElementById('chatArea');
    if (!chatArea) return;

    const run = () => {
        scrollToBottomFrame = null;
        const target = getChatScrollTarget(chatArea);
        const top = target.element.scrollHeight;
        const scrollOptions = { top, behavior: options.smooth ? 'smooth' : 'auto' };
        if (target.isWindow) {
            window.scrollTo(scrollOptions);
        } else {
            target.element.scrollTo(scrollOptions);
        }
    };

    if (options.force) {
        if (scrollToBottomFrame) {
            cancelAnimationFrame(scrollToBottomFrame);
            scrollToBottomFrame = null;
        }
        run();
        return;
    }

    if (!scrollToBottomFrame) {
        scrollToBottomFrame = requestAnimationFrame(run);
    }
}

function getFavicon(url) {
    try {
        const u = new URL(url);
        return `https://www.google.com/s2/favicons?domain=${u.hostname}&sz=16`;
    } catch { return ''; }
}

// ============ Markdown & Rich Rendering ============

// Configure marked.js once — no custom renderer (avoids v4/v5+ API mismatch)
(function setupMarked() {
    if (typeof marked === 'undefined') return;
    marked.use({ gfm: true, breaks: true });
})();

function copyTextToClipboard(text) {
    if (navigator.clipboard && window.isSecureContext) {
        return navigator.clipboard.writeText(text).catch(() => fallbackCopyText(text));
    }

    return fallbackCopyText(text);
}

function fallbackCopyText(text) {
    return new Promise((resolve, reject) => {
        const textArea = document.createElement('textarea');
        textArea.value = text;
        textArea.setAttribute('readonly', '');
        textArea.style.position = 'fixed';
        textArea.style.top = '-9999px';
        textArea.style.left = '-9999px';
        document.body.appendChild(textArea);
        textArea.select();
        try {
            document.execCommand('copy') ? resolve() : reject(new Error('Copy failed'));
        } catch (err) {
            reject(err);
        } finally {
            textArea.remove();
        }
    });
}

// Copy button handler
function copyCodeBlock(btn) {
    const pre = btn.closest('.code-block-wrapper').querySelector('pre');
    const text = pre.innerText || pre.textContent;
    copyTextToClipboard(text).then(() => {
        btn.innerHTML = '<i class="fa-solid fa-check"></i> Copied!';
        btn.classList.add('copied');
        setTimeout(() => {
            btn.innerHTML = '<i class="fa-regular fa-copy"></i> Copy';
            btn.classList.remove('copied');
        }, 2000);
    });
}

// Price pattern highlight (USD, RM, EUR, GBP, SGD, etc.)
function highlightPrices(html) {
    // Only highlight prices that are NOT inside <a> tags or existing spans
    return html.replace(
        /(?<!class="[^"]*price[^"]*">)(\b(?:USD|RM|SGD|MYR|EUR|GBP)\s*[\d,]+(?:\.\d{1,2})?|\$[\d,]+(?:\.\d{1,2})?|£[\d,]+(?:\.\d{1,2})?|€[\d,]+(?:\.\d{1,2})?)/g,
        '<span class="price-badge">$1</span>'
    );
}

function renderMd(text) {
    const raw = String(text ?? '');
    if (typeof marked === 'undefined' || typeof DOMPurify === 'undefined') {
        return escapeHtml(raw).replace(/\n/g, '<br>');
    }
    let html = marked.parse(raw);
    html = highlightPrices(html);
    html = DOMPurify.sanitize(html, { USE_PROFILES: { html: true } });
    return html.replace(/<a\s+href=/g, '<a class="md-link" target="_blank" rel="noopener noreferrer" href=');
}

function normalizePdfUrl(pdfUrl) {
    return safeExternalUrl(pdfUrl);
}

function createPdfDownloadCard(pdfUrl, pdfName = 'PepperReport.pdf', title = 'Your PDF Report is Ready!') {
    const resolvedUrl = normalizePdfUrl(pdfUrl);
    const dlCard = document.createElement('div');
    dlCard.className = 'pdf-download-card';
    dlCard.innerHTML = `
        <div class="pdf-card-icon"><i class="fa-solid fa-file-pdf"></i></div>
        <div class="pdf-card-text">
            <span class="pdf-card-title">${escapeAttr(title)}</span>
            <span class="pdf-card-sub">${escapeAttr(pdfName)}</span>
        </div>
        <button class="pdf-dl-btn" type="button">
            <i class="fa-solid fa-download"></i> Download PDF
        </button>
    `;

    const btn = dlCard.querySelector('.pdf-dl-btn');
    btn.addEventListener('click', async () => {
        const originalHtml = btn.innerHTML;
        btn.disabled = true;
        btn.innerHTML = '<i class="fa-solid fa-circle-notch fa-spin"></i> Downloading...';
        try {
            const res = await fetch(resolvedUrl, { cache: 'no-store' });
            if (!res.ok) throw new Error(`PDF_HTTP_${res.status}`);
            const blob = await res.blob();
            const objectUrl = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = objectUrl;
            a.download = pdfName;
            document.body.appendChild(a);
            a.click();
            a.remove();
            setTimeout(() => URL.revokeObjectURL(objectUrl), 1500);
            btn.innerHTML = '<i class="fa-solid fa-check"></i> Downloaded!';
            btn.style.background = '#16a34a';
        } catch (err) {
            console.error('PDF download failed', err);
            btn.innerHTML = originalHtml;
            btn.disabled = false;
            showToast('PDF download failed. Please generate it again.', true);
        }
    });

    return dlCard;
}

function normalizeAssetUrl(url) {
    return safeExternalUrl(url);
}

function createImageResultCard(imageUrl, imageName = 'generated-image.png', title = 'Generated Image') {
    const resolvedUrl = normalizeAssetUrl(imageUrl);
    const imageCard = document.createElement('div');
    imageCard.className = 'generated-image-card';
    imageCard.innerHTML = `
        <div class="generated-image-preview">
            <img src="${escapeAttr(resolvedUrl)}" alt="${escapeAttr(title)}" loading="lazy">
        </div>
        <div class="generated-image-meta">
            <div class="generated-image-title-row">
                <span class="generated-image-icon"><i class="fa-regular fa-image"></i></span>
                <div class="generated-image-copy">
                    <span class="generated-image-title">${escapeAttr(title)}</span>
                    <span class="generated-image-sub">${escapeAttr(imageName)}</span>
                </div>
            </div>
            <div class="generated-image-actions">
                <a class="generated-image-open" href="${escapeAttr(resolvedUrl)}" target="_blank" rel="noopener noreferrer">
                    <i class="fa-solid fa-up-right-from-square"></i> Open
                </a>
                <button class="generated-image-download" type="button">
                    <i class="fa-solid fa-download"></i> Download
                </button>
            </div>
        </div>
    `;

    const btn = imageCard.querySelector('.generated-image-download');
    btn.addEventListener('click', async () => {
        const originalHtml = btn.innerHTML;
        btn.disabled = true;
        btn.innerHTML = '<i class="fa-solid fa-circle-notch fa-spin"></i> Downloading...';
        try {
            const res = await fetch(resolvedUrl, { cache: 'no-store' });
            if (!res.ok) throw new Error(`IMAGE_HTTP_${res.status}`);
            const blob = await res.blob();
            const objectUrl = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = objectUrl;
            a.download = imageName || 'generated-image.png';
            document.body.appendChild(a);
            a.click();
            a.remove();
            setTimeout(() => URL.revokeObjectURL(objectUrl), 1500);
            btn.innerHTML = '<i class="fa-solid fa-check"></i> Downloaded';
            btn.style.background = '#16a34a';
        } catch (err) {
            console.error('Image download failed', err);
            btn.innerHTML = originalHtml;
            btn.disabled = false;
            showToast('Image download failed. Please generate it again.', true);
        }
    });

    return imageCard;
}

function createGeneratedFileCard(fileUrl, fileName = 'generated-file', fileType = '', title = '') {
    const resolvedUrl = normalizeAssetUrl(fileUrl);
    const icon = getAttachmentIcon(fileName, getMimeTypeForFilename(fileName));
    const displayType = (fileType || (fileName.includes('.') ? fileName.split('.').pop() : 'file') || 'file').toUpperCase();
    const cardTitle = title || tUi('generatedFileTitle', 'Generated File');
    const downloadLabel = tUi('downloadFile', 'Download');
    const openLabel = tUi('openFile', 'Open');
    const fileCard = document.createElement('div');
    fileCard.className = 'generated-file-card';
    fileCard.innerHTML = `
        <div class="generated-file-icon" style="color:${escapeAttr(icon.iconColor)}">
            <i class="fa-solid ${escapeAttr(icon.iconClass)}"></i>
        </div>
        <div class="generated-file-copy">
            <span class="generated-file-title">${escapeAttr(cardTitle)}</span>
            <span class="generated-file-sub">${escapeAttr(displayType)} · ${escapeAttr(fileName)}</span>
        </div>
        <div class="generated-file-actions">
            <a class="generated-file-open" href="${escapeAttr(resolvedUrl)}" target="_blank" rel="noopener noreferrer">
                <i class="fa-solid fa-up-right-from-square"></i> ${escapeAttr(openLabel)}
            </a>
            <button class="generated-file-download" type="button">
                <i class="fa-solid fa-download"></i> ${escapeAttr(downloadLabel)}
            </button>
        </div>
    `;

    const btn = fileCard.querySelector('.generated-file-download');
    btn.addEventListener('click', async () => {
        const originalHtml = btn.innerHTML;
        btn.disabled = true;
        btn.innerHTML = '<i class="fa-solid fa-circle-notch fa-spin"></i>';
        try {
            const res = await fetch(resolvedUrl, { cache: 'no-store' });
            if (!res.ok) throw new Error(`FILE_HTTP_${res.status}`);
            const blob = await res.blob();
            const objectUrl = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = objectUrl;
            a.download = fileName || 'generated-file';
            document.body.appendChild(a);
            a.click();
            a.remove();
            setTimeout(() => URL.revokeObjectURL(objectUrl), 1500);
            btn.innerHTML = '<i class="fa-solid fa-check"></i>';
            btn.style.background = '#16a34a';
        } catch (err) {
            console.error('File download failed', err);
            btn.innerHTML = originalHtml;
            btn.disabled = false;
            showToast(tUi('fileDownloadFailed', 'File download failed. Please generate it again.'), true);
        }
    });

    return fileCard;
}

function createImageGenerationLoader() {
    const lang = getPreferredLanguage();
    const label = lang === 'ms' ? 'Menjana imej...' : 'Generating image...';
    const loader = document.createElement('div');
    loader.className = 'image-generation-loader';

    const grid = document.createElement('div');
    grid.className = 'image-neuron-grid';
    grid.setAttribute('aria-hidden', 'true');

    const cols = 38;
    const rows = 22;
    const clusters = [
        { x: 0.10, y: 0.12, phase: 0.0 },
        { x: 0.78, y: 0.11, phase: 0.6 },
        { x: 0.58, y: 0.43, phase: 1.2 },
        { x: 0.38, y: 0.78, phase: 1.8 },
        { x: 0.83, y: 0.84, phase: 2.4 },
        { x: 0.16, y: 0.94, phase: 3.0 },
    ];

    for (let y = 0; y < rows; y += 1) {
        for (let x = 0; x < cols; x += 1) {
            const nx = x / (cols - 1);
            const ny = y / (rows - 1);
            let best = clusters[0];
            let bestDist = Infinity;
            clusters.forEach(cluster => {
                const dx = nx - cluster.x;
                const dy = ny - cluster.y;
                const dist = Math.sqrt(dx * dx + dy * dy);
                if (dist < bestDist) {
                    bestDist = dist;
                    best = cluster;
                }
            });

            const waveDelay = best.phase + bestDist * 2.1 + ((x * 13 + y * 7) % 17) * 0.018;
            const intensity = Math.max(0.14, 1 - bestDist * 4.5);
            const dot = document.createElement('span');
            dot.className = 'image-neuron-dot';
            dot.style.setProperty('--delay', `-${waveDelay.toFixed(2)}s`);
            dot.style.setProperty('--peak', Math.min(0.86, 0.22 + intensity * 0.74).toFixed(2));
            dot.style.setProperty('--dot-size', `${(2.2 + intensity * 2.4).toFixed(1)}px`);
            grid.appendChild(dot);
        }
    }

    const pill = document.createElement('div');
    pill.className = 'image-generation-label';
    pill.textContent = label;

    loader.appendChild(grid);
    loader.appendChild(pill);
    return loader;
}

function isLikelyImageGenerationRequest(text = '') {
    const raw = String(text || '').trim();
    if (!raw) return false;
    const low = raw.toLowerCase();
    const asksForHelp = /(prompt|prompts|idea|ideas|suggest|recommend|how to|what is|explain|analy[sz]e|describe)/i.test(low)
        || /(提示词|几个|建议|方案|怎么|如何|解释|分析|描述|识别|看图)/.test(raw);
    if (asksForHelp) return false;
    const hasAction = /(generate|create|make|design|draw|illustrate)/i.test(low)
        || /(生成|制作|创建|设计|画|绘制)/.test(raw);
    const hasVisualAsset = /(image|picture|photo|illustration|poster|logo|icon|banner|wallpaper|png|jpe?g|svg)/i.test(low)
        || /(图片|图像|照片|插画|海报|标志|图标|横幅|壁纸|宣传图)/.test(raw);
    return hasAction && hasVisualAsset;
}

const GMAIL_PREVIEW_RE = /\[GMAIL_PREVIEW:([A-Za-z0-9_-]+={0,2})\]/;

function decodeGmailPreviewPayload(text) {
    const match = String(text || '').match(GMAIL_PREVIEW_RE);
    if (!match) return null;
    try {
        const padded = match[1] + '='.repeat((4 - (match[1].length % 4)) % 4);
        const normalized = padded.replace(/-/g, '+').replace(/_/g, '/');
        const binary = atob(normalized);
        const bytes = Uint8Array.from(binary, ch => ch.charCodeAt(0));
        return JSON.parse(new TextDecoder().decode(bytes));
    } catch (err) {
        console.warn('Unable to decode Gmail preview payload:', err);
        return null;
    }
}

function stripGmailPreviewPayload(text) {
    return String(text || '').replace(GMAIL_PREVIEW_RE, '').trim();
}

function submitAgentCommand(command) {
    const fakeInput = document.getElementById('userInput');
    const submit = document.getElementById('submitBtn');
    if (fakeInput && submit) {
        fakeInput.value = command;
        submit.click();
    }
}

function createGmailActionCard() {
    const gmailCard = document.createElement('div');
    gmailCard.className = 'gmail-confirm-card';
    gmailCard.innerHTML = `
        <div class="gmail-card-actions">
            <button class="gmail-confirm-btn" type="button">
                <i class="fa-solid fa-paper-plane"></i> Confirm Send
            </button>
            <button class="gmail-cancel-btn" type="button">
                <i class="fa-solid fa-xmark"></i> Cancel
            </button>
        </div>
    `;
    const confirmBtn = gmailCard.querySelector('.gmail-confirm-btn');
    const cancelBtn = gmailCard.querySelector('.gmail-cancel-btn');

    confirmBtn.addEventListener('click', () => {
        confirmBtn.disabled = true;
        cancelBtn.disabled = true;
        confirmBtn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Sending...';
        submitAgentCommand('[CONFIRM_GMAIL_SEND]');
    });

    cancelBtn.addEventListener('click', () => {
        confirmBtn.disabled = true;
        cancelBtn.disabled = true;
        cancelBtn.innerHTML = '<i class="fa-solid fa-check"></i> Cancelled';
        submitAgentCommand('[CANCEL_GMAIL_SEND]');
    });

    return gmailCard;
}

function createGmailPreviewCard(payload) {
    if (!payload) return null;
    const lang = payload.lang || getPreferredLanguage();
    const labels = {
        zh: { title: '邮件草稿', to: '收件人', subject: '主题', attach: '附件', none: '无附件', attached: '已附加', body: '正文预览', expand: '展开全文', collapse: '收起全文' },
        ms: { title: 'Draf E-mel', to: 'Kepada', subject: 'Subjek', attach: 'Lampiran', none: 'Tiada', attached: 'Dilampirkan', body: 'Pratonton', expand: 'Lihat penuh', collapse: 'Ringkaskan' },
        en: { title: 'Email Draft', to: 'To', subject: 'Subject', attach: 'Attachment', none: 'None', attached: 'Attached', body: 'Preview', expand: 'Expand', collapse: 'Collapse' }
    };
    const l = labels[lang] || labels.en;
    const body = String(payload.body || '');
    const isLong = body.length > 420 || body.split('\n').length > 9;
    const card = document.createElement('div');
    card.className = 'gmail-preview-card';
    card.innerHTML = `
        <div class="gmail-preview-head">
            <div class="gmail-preview-mark"><i class="fa-solid fa-envelope-open-text"></i></div>
            <div>
                <div class="gmail-preview-kicker">Google Workspace</div>
                <div class="gmail-preview-title">${escapeAttr(l.title)}</div>
            </div>
        </div>
        <div class="gmail-preview-meta">
            <div><span>${escapeAttr(l.to)}</span><strong>${escapeAttr(payload.recipient || '-')}</strong></div>
            <div><span>${escapeAttr(l.subject)}</span><strong>${escapeAttr(payload.subject || '-')}</strong></div>
            <div><span>${escapeAttr(l.attach)}</span><strong>${payload.hasAttachment ? escapeAttr(`${l.attached}${payload.attachmentName ? ` · ${payload.attachmentName}` : ''}`) : escapeAttr(l.none)}</strong></div>
        </div>
        <div class="gmail-preview-body-label">${escapeAttr(l.body)}</div>
        <div class="gmail-preview-body ${isLong ? 'collapsed' : ''}">${escapeAttr(body).replace(/\n/g, '<br>')}</div>
        ${isLong ? `<button class="gmail-preview-toggle" type="button"><i class="fa-solid fa-chevron-down"></i> ${escapeAttr(l.expand)}</button>` : ''}
    `;
    const toggle = card.querySelector('.gmail-preview-toggle');
    if (toggle) {
        toggle.addEventListener('click', () => {
            const bodyEl = card.querySelector('.gmail-preview-body');
            const collapsed = bodyEl.classList.toggle('collapsed');
            toggle.innerHTML = collapsed
                ? `<i class="fa-solid fa-chevron-down"></i> ${escapeAttr(l.expand)}`
                : `<i class="fa-solid fa-chevron-up"></i> ${escapeAttr(l.collapse)}`;
        });
    }
    return card;
}

function getProgressStages(mode) {
    if (mode === 'agent') {
        return ['Planning workspace action...', 'Reading active context...', 'Preparing tool call...', 'Composing final response...'];
    }
    if (mode === 'web') {
        return ['Planning search...', 'Reading live sources...', 'Ranking evidence...', 'Synthesizing answer...'];
    }
    if (mode === 'think') {
        return ['Mapping the problem...', 'Checking assumptions...', 'Building reasoning path...', 'Writing answer...'];
    }
    return ['Warming up model...', 'Reading context...', 'Choosing answer depth...', 'Drafting response...'];
}

function startProgressAnimation(thinkHeader, thinkDurationEl, mode) {
    if (!thinkHeader) return { stop() {} };
    const labelEl = thinkHeader.querySelector('.think-label');
    const iconEl = thinkHeader.querySelector('.think-icon');
    const stages = getProgressStages(mode);
    let stageIndex = 0;
    const startedAt = Date.now();
    if (labelEl) labelEl.innerText = stages[0];
    if (iconEl) iconEl.innerHTML = '<i class="fa-solid fa-sparkles fa-spin"></i>';
    const tick = setInterval(() => {
        stageIndex = (stageIndex + 1) % stages.length;
        if (labelEl) labelEl.innerText = stages[stageIndex];
        if (thinkDurationEl) {
            thinkDurationEl.innerText = `${Math.max(1, Math.floor((Date.now() - startedAt) / 1000))}s`;
        }
    }, 2200);
    return {
        stop(finalLabel = '') {
            clearInterval(tick);
            if (finalLabel && labelEl) labelEl.innerText = finalLabel;
        }
    };
}

function unwrapPriceBadgesInTables(root) {
    if (!root) return;
    root.querySelectorAll('table .price-badge').forEach(span => {
        span.replaceWith(document.createTextNode(span.textContent || ''));
    });
}

function wrapMarkdownTables(root) {
    if (!root) return;
    root.querySelectorAll('table').forEach(table => {
        if (table.parentElement && table.parentElement.classList.contains('table-scroll-wrap')) return;
        const wrapper = document.createElement('div');
        wrapper.className = 'table-scroll-wrap';
        table.parentNode.insertBefore(wrapper, table);
        wrapper.appendChild(table);
    });
}

// Apply Prism.js + KaTeX + code copy buttons after setting innerHTML
function applyRichFormatting(el) {
    if (!el) return;

    unwrapPriceBadgesInTables(el);
    wrapMarkdownTables(el);

    // Wrap bare <pre> blocks with code-block-wrapper + copy button header
    el.querySelectorAll('pre').forEach(pre => {
        if (pre.closest('.code-block-wrapper')) return; // already wrapped
        const codeEl = pre.querySelector('code');
        const langClass = codeEl ? (codeEl.className || '') : '';
        const lang = langClass.replace(/language-/g, '').trim() || 'code';

        const wrapper = document.createElement('div');
        wrapper.className = 'code-block-wrapper';

        const header = document.createElement('div');
        header.className = 'code-block-header';
        header.innerHTML = `
            <span class="code-lang-label">${escapeHtml(lang)}</span>
            <button class="code-copy-btn" onclick="copyCodeBlock(this)" title="Copy code">
                <i class="fa-regular fa-copy"></i> Copy
            </button>`;

        pre.parentNode.insertBefore(wrapper, pre);
        wrapper.appendChild(header);
        wrapper.appendChild(pre);
    });

    // Prism.js syntax highlighting
    if (typeof Prism !== 'undefined') {
        el.querySelectorAll('pre code').forEach(block => {
            Prism.highlightElement(block);
        });
    }

    // KaTeX math rendering
    if (typeof renderMathInElement !== 'undefined') {
        try {
            renderMathInElement(el, {
                delimiters: [
                    { left: '$$', right: '$$', display: true },
                    { left: '$', right: '$', display: false },
                    { left: '\\(', right: '\\)', display: false },
                    { left: '\\[', right: '\\]', display: true }
                ],
                throwOnError: false
            });
        } catch(e) {}
    }
}

// ============ User Message & Actions ============
function createActionButtons(wrapper, msgIndex, feedbackVal, isAssistant, msgText) {
    const actions = document.createElement('div');
    actions.className = isAssistant ? 'msg-action-bar assistant-msg-action-bar' : 'msg-action-bar user-msg-action-bar';

    const copyIcon = `
        <svg class="message-action-svg" viewBox="0 0 24 24" aria-hidden="true">
            <rect x="8.25" y="8.25" width="10.5" height="10.5" rx="2.15"></rect>
            <path d="M5.75 15.75h-.5A2.25 2.25 0 0 1 3 13.5V5.25A2.25 2.25 0 0 1 5.25 3h8.25A2.25 2.25 0 0 1 15.75 5.25v.5"></path>
        </svg>`;
    const checkIcon = `
        <svg class="message-action-svg" viewBox="0 0 24 24" aria-hidden="true">
            <path d="M5 12.5l4.2 4.2L19 7"></path>
        </svg>`;
    const editIcon = `
        <svg class="message-action-svg" viewBox="0 0 24 24" aria-hidden="true">
            <path d="M4.75 19.25h4.1L18.9 9.2a2.05 2.05 0 0 0-2.9-2.9L5.95 16.35 4.75 19.25Z"></path>
            <path d="m14.65 7.65 2.9 2.9"></path>
        </svg>`;
    const regenerateIcon = `
        <svg class="message-action-svg" viewBox="0 0 24 24" aria-hidden="true">
            <path d="M20 11a8 8 0 1 0-2.35 5.65"></path>
            <path d="M20 5v6h-6"></path>
        </svg>`;
    const resumeIcon = `
        <svg class="message-action-svg" viewBox="0 0 24 24" aria-hidden="true">
            <path d="M8 5.75v12.5L18 12Z"></path>
        </svg>`;
    const thumbsUpIcon = `
        <svg class="message-action-svg" viewBox="0 0 24 24" aria-hidden="true">
            <path d="M7.25 10v10H4.9A1.9 1.9 0 0 1 3 18.1v-6.2A1.9 1.9 0 0 1 4.9 10h2.35Z"></path>
            <path d="M7.25 10l4.35-6.05a1.45 1.45 0 0 1 2.6 1.1L13.35 9H18a2 2 0 0 1 1.95 2.42l-1.2 5.6A2.45 2.45 0 0 1 16.35 19H7.25"></path>
        </svg>`;
    const thumbsDownIcon = `
        <svg class="message-action-svg" viewBox="0 0 24 24" aria-hidden="true">
            <path d="M7.25 14V4H4.9A1.9 1.9 0 0 0 3 5.9v6.2A1.9 1.9 0 0 0 4.9 14h2.35Z"></path>
            <path d="M7.25 14l4.35 6.05a1.45 1.45 0 0 0 2.6-1.1L13.35 15H18a2 2 0 0 0 1.95-2.42l-1.2-5.6A2.45 2.45 0 0 0 16.35 5H7.25"></path>
        </svg>`;

    const copyBtn = document.createElement('button');
    copyBtn.className = 'msg-action-btn';
    copyBtn.setAttribute('aria-label', isAssistant ? 'Copy response' : 'Copy message');
    copyBtn.innerHTML = copyIcon;
    copyBtn.onclick = async () => {
        await copyTextToClipboard(msgText);
        copyBtn.innerHTML = checkIcon;
        setTimeout(() => { copyBtn.innerHTML = copyIcon; }, 1500);
    };
    actions.appendChild(copyBtn);

    if (isAssistant) {
        const regenBtn = document.createElement('button');
        regenBtn.className = 'msg-action-btn';
        regenBtn.setAttribute('aria-label', 'Regenerate response');
        regenBtn.innerHTML = regenerateIcon;
        regenBtn.onclick = () => {
            if (isGenerating) return;
            const targetAssistantMsg = chatMessages[msgIndex];
            let prevUserMsg = chatMessages[msgIndex - 1];
            if (!prevUserMsg || prevUserMsg.role !== 'user') return;

            chatMessages = chatMessages.slice(0, msgIndex - 1);

            let sibling = wrapper.previousElementSibling;
            while(sibling) {
                const nxt = sibling.nextElementSibling;
                sibling.remove();
                sibling = nxt;
            }
            userInput.value = prevUserMsg.content;

            // Preserve attachments for regeneration without needing to re-upload
            if (prevUserMsg.attachments && prevUserMsg.attachments.length > 0) {
                // Attach them to the hidden pendingAttachments variable so handleSend picks them up
                // Wait, handleSend expects them in pendingFiles, but pendingFiles are File objects.
                // Instead, we can inject a temporary flag so handleSend knows to reuse them.
                window._regenerateAttachments = prevUserMsg.attachments;
            }
            if (isAgentMode && targetAssistantMsg?.pdf_url) {
                window._forceRegeneratePdf = true;
            }

            handleSend();
        };
        actions.appendChild(regenBtn);

        const resumeBtn = document.createElement('button');
        resumeBtn.className = 'msg-action-btn';
        resumeBtn.setAttribute('aria-label', 'Resume generation');
        resumeBtn.innerHTML = resumeIcon;
        resumeBtn.onclick = () => {
            if (isGenerating) return;
            handleSend(true, msgIndex);
        };
        actions.appendChild(resumeBtn);

        const likeBtn = document.createElement('button');
        likeBtn.className = `msg-action-btn ${feedbackVal === 1 ? 'active' : ''}`;
        likeBtn.setAttribute('aria-label', 'Good response');
        likeBtn.innerHTML = thumbsUpIcon;

        const dislikeBtn = document.createElement('button');
        dislikeBtn.className = `msg-action-btn ${feedbackVal === -1 ? 'active' : ''}`;
        dislikeBtn.setAttribute('aria-label', 'Bad response');
        dislikeBtn.innerHTML = thumbsDownIcon;

        const sendFeedback = async (val) => {
            let newVal = ((likeBtn.classList.contains('active') && val === 1) || (dislikeBtn.classList.contains('active') && val === -1)) ? 0 : val;
            likeBtn.classList.toggle('active', newVal === 1);
            dislikeBtn.classList.toggle('active', newVal === -1);
            if (currentChatId) {
                try {
                    await fetch('/api/chat/feedback', {
                        method: 'POST',
                        headers: authenticatedHeaders({'Content-Type': 'application/json'}),
                        body: JSON.stringify({ chat_id: currentChatId, msg_index: msgIndex, rating: newVal })
                    });
                } catch(e) { console.error(e); }
            }
        };
        likeBtn.onclick = () => sendFeedback(1);
        dislikeBtn.onclick = () => sendFeedback(-1);

        actions.appendChild(likeBtn);
        actions.appendChild(dislikeBtn);
    } else {
        const editBtn = document.createElement('button');
        editBtn.className = 'msg-action-btn';
        editBtn.setAttribute('aria-label', 'Edit message');
        editBtn.innerHTML = editIcon;
        editBtn.onclick = () => {
            if (isGenerating) return;
            const input = document.createElement('textarea');
            input.className = 'edit-input user-edit-input';
            input.rows = 1;
            input.value = chatMessages[msgIndex].content;

            const userBubble = wrapper.querySelector('.message-bubble.user');
            if (userBubble) userBubble.style.display = 'none';
            wrapper.classList.add('user-msg-editing');
            actions.parentElement.insertBefore(input, actions);
            const resizeEditInput = () => {
                input.style.height = 'auto';
                input.style.height = `${Math.min(input.scrollHeight, 220)}px`;
            };
            requestAnimationFrame(() => {
                resizeEditInput();
                input.focus();
                input.setSelectionRange(input.value.length, input.value.length);
            });
            let edited = false;

            const restoreEdit = () => {
                input.remove();
                if (userBubble) userBubble.style.display = '';
                wrapper.classList.remove('user-msg-editing');
            };

            const submitEdit = () => {
                if (edited) return;
                edited = true;
                const newText = input.value.trim();
                restoreEdit();

                if (newText && newText !== chatMessages[msgIndex].content) {
                    chatMessages = chatMessages.slice(0, msgIndex);
                    let sibling = wrapper;
                    let siblingsToRemove = [];
                    while(sibling) {
                        siblingsToRemove.push(sibling);
                        sibling.style.transition = 'all 0.4s cubic-bezier(0.16, 1, 0.3, 1)';
                        sibling.style.opacity = '0';
                        sibling.style.transform = 'translateY(10px) scale(0.98)';
                        sibling = sibling.nextElementSibling;
                    }
                    setTimeout(() => {
                        siblingsToRemove.forEach(el => el.remove());
                        userInput.value = newText;
                        handleSend();
                    }, 400);
                }
            };
            const cancelEdit = () => {
                if (edited) return;
                edited = true;
                restoreEdit();
            };
            input.addEventListener('input', resizeEditInput);
            input.addEventListener('keydown', (e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault();
                    submitEdit();
                } else if (e.key === 'Escape') {
                    e.preventDefault();
                    cancelEdit();
                }
            });
            input.addEventListener('blur', submitEdit);
        };
        actions.appendChild(editBtn);
    }

    const actionHost = isAssistant ? wrapper : (wrapper.querySelector('.user-message-shell') || wrapper);
    actionHost.appendChild(actions);
}

function appendMessage(text, role, msgObj = null, msgIndex = null, feedbackVal = 0, skipScroll = false) {
    if (role === 'user') {
        const wrapper = document.createElement('div');
        wrapper.className = 'user-msg-wrapper';
        if (text === '[CONFIRM_GMAIL_SEND]' || text === '[CANCEL_GMAIL_SEND]') {
            wrapper.style.display = 'none';
        }

        // Render attachments as separate stacked cards ABOVE the text bubble
        if (msgObj && msgObj.attachments && msgObj.attachments.length > 0) {
            const attBlock = document.createElement('div');
            attBlock.className = 'user-msg-attachment-block';
            msgObj.attachments.forEach(att => {
                const card = createAttachmentCard(att);
                attBlock.appendChild(card);
            });
            wrapper.appendChild(attBlock);
        }

        const bubble = document.createElement('div');
        bubble.className = 'message-bubble user';
        bubble.innerHTML = `<div class="markdown-content">` + renderMd(text) + `</div>`;
        const messageShell = document.createElement('div');
        messageShell.className = 'user-message-shell';
        messageShell.appendChild(bubble);
        wrapper.appendChild(messageShell);

        requestAnimationFrame(() => {
            const bubbleRect = bubble.getBoundingClientRect();
            const isLongMessage = bubbleRect.width >= 360 || bubble.scrollHeight > 64 || String(text).trim().length > 24;
            messageShell.classList.toggle('user-message-shell--long', isLongMessage);
            messageShell.classList.toggle('user-message-shell--short', !isLongMessage);
        });

        if (msgIndex !== null) {
            createActionButtons(wrapper, msgIndex, 0, false, text);
        }
        messagesContainer.appendChild(wrapper);
    } else {
        const wrapper = document.createElement('div');
        wrapper.className = 'assistant-msg-wrapper';

        let displayThink = '';
        let displayAnswer = text;
        const startTag = '<think>';
        const endTag = '</think>';
        const startIdx = text.indexOf(startTag);
        const endIdx = text.indexOf(endTag);

        if (startIdx !== -1 && endIdx !== -1) {
            displayThink = text.substring(startIdx + startTag.length, endIdx).trim();
            displayAnswer = (text.substring(0, startIdx) + text.substring(endIdx + endTag.length)).trim();
        } else if (startIdx !== -1) {
            displayThink = text.substring(startIdx + startTag.length).trim();
            displayAnswer = text.substring(0, startIdx).trim();
        } else if (endIdx !== -1) {
            displayThink = text.substring(0, endIdx).trim();
            displayAnswer = text.substring(endIdx + endTag.length).trim();
        }

        if (displayThink || (msgObj && msgObj.sources && msgObj.sources.length > 0)) {
            const tDiv = document.createElement('div');
            tDiv.className = 'think-wrapper done collapsed';

            let htmlInner = '';
            if (displayThink) {
                htmlInner += `
                    <div class="think-header" onclick="this.parentElement.classList.toggle('collapsed')">
                        <div class="think-icon"><i class="fa-solid fa-brain"></i></div>
                        <span class="think-label">Think Process</span>
                        <i class="fa-solid fa-chevron-down think-toggle-arrow"></i>
                    </div>
                    <div class="think-content markdown-content">${renderMd(displayThink)}</div>`;
            }
            if (msgObj && msgObj.sources && msgObj.sources.length > 0) {
                htmlInner += `
                <div class="think-sources-bar" style="display: block; opacity: 1; max-height: unset; padding: 1rem 1.4rem; border-top: 1px solid var(--outline-variant);">
                    <div class="sources-header">
                        <i class="fa-solid fa-magnifying-glass"></i> Read ${msgObj.sources.length} sources
                    </div>
                    <div class="sources-scroll">
                        ${msgObj.sources.map(s => {
                            let domain = '';
                            try { domain = new URL(s.url).hostname.replace('www.',''); } catch {}
                            const sourceUrl = safeExternalUrl(s.url);
                            if (!sourceUrl) return '';
                            const favUrl = getFavicon(sourceUrl);
                            return `
                                <a class="source-card" href="${escapeAttr(sourceUrl)}" target="_blank" rel="noopener noreferrer">
                                    ${favUrl ? `<img class="source-card-favicon" src="${escapeAttr(favUrl)}" alt="">` : ''}
                                    <div class="source-card-text">
                                        <span class="source-card-title">${escapeHtml(s.title || domain)}</span>
                                        <span class="source-card-domain">${escapeHtml(domain)}</span>
                                    </div>
                                </a>`;
                        }).join('')}
                    </div>
                </div>`;
            }
            tDiv.innerHTML = htmlInner;
            wrapper.appendChild(tDiv);
        }
        if (displayAnswer) {
            const aDiv = document.createElement('div');
            aDiv.className = 'message-bubble assistant markdown-content';
            let hasGmailPending = false;
            const gmailPreviewPayload = decodeGmailPreviewPayload(displayAnswer);
            if (gmailPreviewPayload) {
                displayAnswer = stripGmailPreviewPayload(displayAnswer);
            }
            if (displayAnswer.includes('[GMAIL_CONFIRM_PENDING]')) {
                displayAnswer = displayAnswer.replace('[GMAIL_CONFIRM_PENDING]', '');
                hasGmailPending = true;
            }
            aDiv.innerHTML = renderMd(displayAnswer);
            wrapper.appendChild(aDiv);

            if (gmailPreviewPayload) {
                const previewCard = createGmailPreviewCard(gmailPreviewPayload);
                if (previewCard) wrapper.appendChild(previewCard);
            }

            if (hasGmailPending) {
                wrapper.appendChild(createGmailActionCard());
            }
        }

        if (msgIndex !== null) {
            createActionButtons(wrapper, msgIndex, feedbackVal, true, text);
        }

        // Re-render PDF download card if this message generated a PDF
        if (msgObj && msgObj.pdf_url) {
            const pdfName = msgObj.pdf_name || 'PepperReport.pdf';
            const dlCard = createPdfDownloadCard(msgObj.pdf_url, pdfName, 'PDF Report Available');
            wrapper.appendChild(dlCard);
        }

        if (msgObj && msgObj.generated_image_url) {
            const imageName = msgObj.generated_image_name || 'generated-image.png';
            const imageCard = createImageResultCard(msgObj.generated_image_url, imageName, 'Generated Image');
            wrapper.appendChild(imageCard);
        }

        if (msgObj && msgObj.generated_file_url && !(msgObj.generated_file_type === 'pdf' && msgObj.pdf_url)) {
            const fileName = msgObj.generated_file_name || 'generated-file';
            const fileCard = createGeneratedFileCard(
                msgObj.generated_file_url,
                fileName,
                msgObj.generated_file_type || '',
                tUi('generatedFileTitle', 'Generated File')
            );
            wrapper.appendChild(fileCard);
        }

        messagesContainer.appendChild(wrapper);
    }
    if (!skipScroll) scrollToBottom();
}

// ============ Main Send Handler ============
async function handleSend(isResume = false, resumeIndex = null) {
    if (isGenerating) {
        if (currentAbortController) currentAbortController.abort();
        return;
    }

    let text = userInput.value.trim();

    if (!isResume && isPaused) {
        if (text) {
            isPaused = false;
            pausedMsgIndex = null;
            submitBtn.className = 'submit-btn';
        } else {
            isResume = true;
            resumeIndex = pausedMsgIndex;
            isPaused = false;
            pausedMsgIndex = null;
            submitBtn.className = 'submit-btn';
        }
    }

    if (!isResume && !text && pendingFiles.length === 0) return;

    if (!isResume && !currentUserId) {
        showGuestQuestionAuthModal();
        return;
    }

    if (isResume) {
        isPaused = false;
        pausedMsgIndex = null;
        submitBtn.className = 'submit-btn';
    }

    let assistantWrapper = null;
    let thinkWrapper = null;
    let thinkHeader = null;
    let thinkContent = null;
    let thinkSourcesBar = null;
    let contentBox = null;
    let thinkStartTime = null;
    let thinkTimerInterval = null;
    let thinkDurationEl = null;

    let rawAccumText = '';
    let currentSources = [];
    let generatedPdf = null;
    let generatedImage = null;
    let generatedFile = null;
    let imageLoadingCard = null;
    let imageOnlyProgressStream = false;
    let frontendThinkAccum = '';
    let frontendAnswerAccum = '';

    const makeAssistantMsgStore = () => {
        const msgStore = { role: 'assistant', content: rawAccumText, sources: currentSources };
        if (generatedPdf && generatedPdf.pdf_url) {
            msgStore.pdf_url = generatedPdf.pdf_url;
            msgStore.pdf_name = generatedPdf.pdf_name || 'PepperReport.pdf';
        }
        if (generatedImage && generatedImage.image_url) {
            msgStore.generated_image_url = generatedImage.image_url;
            msgStore.generated_image_name = generatedImage.image_name || 'generated-image.png';
        }
        if (generatedFile && generatedFile.file_url) {
            msgStore.generated_file_url = generatedFile.file_url;
            msgStore.generated_file_name = generatedFile.file_name || 'generated-file';
            msgStore.generated_file_type = generatedFile.file_type || '';
            msgStore.generated_file_content_type = generatedFile.content_type || '';
            if (generatedFile.file_type === 'pdf') {
                msgStore.pdf_url = generatedFile.pdf_url || generatedFile.file_url;
                msgStore.pdf_name = generatedFile.file_name || 'generated.pdf';
            }
        }
        return msgStore;
    };

    if (!isResume) {

        if (logoContainer.style.opacity !== '0') {
            logoContainer.style.opacity = '0';
            document.querySelector('.app-container').classList.remove('centered-landing');
            setTimeout(() => { logoContainer.style.display = 'none'; }, 500);
        }

        let finalAttachments = [];
        if (pendingFiles.length > 0) {
            submitBtn.className = 'submit-btn answering-state';
            submitBtn.innerHTML = '<i class="fa-solid fa-circle-notch fa-spin"></i>';
            const formData = new FormData();
            pendingFiles.forEach(f => formData.append('files', f));
            try {
                const upRes = await fetch('/api/upload_files', {
                    method: 'POST',
                    headers: authenticatedHeaders(),
                    body: formData,
                });
                const upData = await upRes.json();
                if (upRes.ok && upData.status === 'success') {
                    finalAttachments = upData.files;
                    if (finalAttachments.some(isRecentFileAttachment)) invalidateRecentFilesCache();
                }
            } catch (e) {
                console.error("Upload failed", e);
            }
            pendingFiles = [];
            renderAttachmentsPreview();
        } else if (window._regenerateAttachments) {
            // Restore attachments from the regenerated message
            finalAttachments = window._regenerateAttachments;
            window._regenerateAttachments = null;
        }

        if (!text && finalAttachments.some(isImageAttachment)) {
            text = getDefaultImageAnalysisPrompt();
        }

        let newMsg = { role: 'user', content: text };
        if (finalAttachments.length > 0) {
            newMsg.attachments = finalAttachments;
        }

        chatMessages.push(newMsg);
        appendMessage(text, 'user', newMsg, chatMessages.length - 1);
        userInput.value = '';
        resizeComposer();

        assistantWrapper = document.createElement('div');
        assistantWrapper.className = 'assistant-msg-wrapper';
        messagesContainer.appendChild(assistantWrapper);
    } else {
        chatMessages = chatMessages.slice(0, resumeIndex + 1);
        const partialAssistantMsg = chatMessages[resumeIndex];
        if (!partialAssistantMsg || partialAssistantMsg.role !== 'assistant') return;

        rawAccumText = partialAssistantMsg.content || '';
        currentSources = partialAssistantMsg.sources || [];

        const startTag = '<think>';
        const endTag = '</think>';
        const startIdx = rawAccumText.indexOf(startTag);
        const endIdx = rawAccumText.indexOf(endTag);

        if (startIdx !== -1 && endIdx !== -1) {
            frontendThinkAccum = rawAccumText.substring(startIdx + startTag.length, endIdx);
            frontendAnswerAccum = (rawAccumText.substring(0, startIdx) + rawAccumText.substring(endIdx + endTag.length));
        } else if (startIdx !== -1) {
            frontendThinkAccum = rawAccumText.substring(startIdx + startTag.length);
            frontendAnswerAccum = rawAccumText.substring(0, startIdx);
        } else if (endIdx !== -1) {
            frontendThinkAccum = rawAccumText.substring(0, endIdx);
            frontendAnswerAccum = rawAccumText.substring(endIdx + endTag.length);
        } else {
            frontendAnswerAccum = rawAccumText;
        }

        assistantWrapper = messagesContainer.children[resumeIndex];
        assistantWrapper.innerHTML = '';
    }

    isGenerating = true;
    currentAbortController = new AbortController();
    // In agent mode, thinking is always on, web is always off
    const effectiveThinkMode = supportsThinkMode && (isAgentMode ? true : isThinkMode);
    const effectiveWebMode = isAgentMode ? false : isWebMode;
    const passiveProgressMode = !effectiveThinkMode && !effectiveWebMode;
    const likelyImageOnlyProgress = !isResume && isLikelyImageGenerationRequest(text);
    const progressMode = isAgentMode ? 'agent' : (effectiveWebMode ? 'web' : (effectiveThinkMode ? 'think' : 'normal'));
    submitBtn.className = 'submit-btn ' + ((effectiveThinkMode || effectiveWebMode && !frontendAnswerAccum) ? 'thinking-state' : 'answering-state');
    submitBtn.innerHTML = '<i class="fa-solid fa-pause"></i>';

    const assistantContainer = document.createElement('div');
    assistantContainer.className = 'message-bubble assistant';
    assistantWrapper.appendChild(assistantContainer);

    let progressController = null;
    let passiveProgressHidden = false;

    if (!likelyImageOnlyProgress && (effectiveThinkMode || effectiveWebMode || (!isResume && !frontendAnswerAccum))) {
        thinkWrapper = document.createElement('div');
        thinkWrapper.className = 'think-wrapper in-progress';
        if (passiveProgressMode) thinkWrapper.classList.add('passive-progress');
        if (!isResume && !passiveProgressMode) thinkWrapper.classList.add('collapsed');

        thinkHeader = document.createElement('div');
        thinkHeader.className = 'think-header';

        let initialLabel = (isResume && frontendAnswerAccum) ? 'Thought Process' : (effectiveWebMode ? 'Searching the web...' : (isAgentMode ? 'Preparing agent...' : 'Preparing response...'));
        let initialIcon = (isResume && frontendAnswerAccum) ? '<i class="fa-solid fa-circle-check"></i>' : (effectiveWebMode ? '<i class="fa-solid fa-globe fa-spin"></i>' : '<i class="fa-solid fa-sparkles fa-spin"></i>');

        thinkHeader.innerHTML = `
            <span class="think-icon">${initialIcon}</span>
            <span class="think-label">${initialLabel}</span>
            <span class="think-duration"></span>
            <span class="think-toggle-arrow"><i class="fa-solid fa-chevron-down"></i></span>
        `;
        thinkDurationEl = thinkHeader.querySelector('.think-duration');

        thinkContent = document.createElement('div');
        thinkContent.className = 'think-content markdown-content';
        if (isResume) thinkContent.innerHTML = renderMd(frontendThinkAccum);

        thinkSourcesBar = document.createElement('div');
        thinkSourcesBar.className = 'think-sources-bar';

        thinkWrapper.appendChild(thinkHeader);
        thinkWrapper.appendChild(thinkSourcesBar);
        thinkWrapper.appendChild(thinkContent);
        assistantContainer.appendChild(thinkWrapper);

        thinkHeader.addEventListener('click', () => {
            thinkWrapper.classList.toggle('collapsed');
        });

        if (!isResume) {
            progressController = startProgressAnimation(thinkHeader, thinkDurationEl, progressMode);
        }

        if (isResume && frontendThinkAccum.trim().length > 0 && !frontendAnswerAccum) {
            thinkWrapper.style.display = 'block';
        } else if (isResume && frontendThinkAccum.trim().length === 0 && !frontendAnswerAccum) {
            // Nothing yet
        } else if (isResume && frontendThinkAccum.trim().length === 0) {
            thinkWrapper.style.display = 'none';
        } else if (isResume && frontendAnswerAccum) {
            thinkWrapper.classList.add('done', 'collapsed');
        }
    }

    contentBox = document.createElement('div');
    contentBox.className = 'markdown-content answer-content';
    if (isResume) contentBox.innerHTML = renderMd(frontendAnswerAccum);
    assistantContainer.appendChild(contentBox);

    const LIVE_RENDER_INTERVAL_MS = 120;
    let answerRenderTimer = null;
    let thinkRenderTimer = null;
    let lastAnswerRenderAt = 0;
    let lastThinkRenderAt = 0;

    const renderAnswerNow = () => {
        if (!contentBox) return;
        contentBox.innerHTML = renderMd(frontendAnswerAccum);
        lastAnswerRenderAt = performance.now();
        answerRenderTimer = null;
    };

    const renderThinkNow = () => {
        if (!thinkContent) return;
        thinkContent.innerHTML = renderMd(frontendThinkAccum);
        lastThinkRenderAt = performance.now();
        thinkRenderTimer = null;
    };

    const scheduleAnswerRender = (force = false) => {
        if (!contentBox) return;
        if (force) {
            if (answerRenderTimer) clearTimeout(answerRenderTimer);
            answerRenderTimer = null;
            renderAnswerNow();
            return;
        }
        const wait = Math.max(0, LIVE_RENDER_INTERVAL_MS - (performance.now() - lastAnswerRenderAt));
        if (wait === 0) {
            if (answerRenderTimer) {
                clearTimeout(answerRenderTimer);
                answerRenderTimer = null;
            }
            renderAnswerNow();
        } else if (!answerRenderTimer) {
            answerRenderTimer = setTimeout(renderAnswerNow, wait);
        }
    };

    const scheduleThinkRender = (force = false) => {
        if (!thinkContent) return;
        if (force) {
            if (thinkRenderTimer) clearTimeout(thinkRenderTimer);
            thinkRenderTimer = null;
            renderThinkNow();
            return;
        }
        const wait = Math.max(0, LIVE_RENDER_INTERVAL_MS - (performance.now() - lastThinkRenderAt));
        if (wait === 0) {
            if (thinkRenderTimer) {
                clearTimeout(thinkRenderTimer);
                thinkRenderTimer = null;
            }
            renderThinkNow();
        } else if (!thinkRenderTimer) {
            thinkRenderTimer = setTimeout(renderThinkNow, wait);
        }
    };

    const showImageGenerationLoader = () => {
        if (!contentBox || imageLoadingCard) return;
        contentBox.innerHTML = '';
        imageLoadingCard = createImageGenerationLoader();
        contentBox.appendChild(imageLoadingCard);
        scrollToBottom();
    };

    const removeImageGenerationLoader = () => {
        if (!imageLoadingCard) return;
        imageLoadingCard.remove();
        imageLoadingCard = null;
    };

    const hideImageThinkPanel = () => {
        if (thinkWrapper) {
            thinkWrapper.style.display = 'none';
            thinkWrapper.classList.remove('in-progress');
        }
        if (thinkTimerInterval) {
            clearInterval(thinkTimerInterval);
            thinkTimerInterval = null;
        }
        if (thinkDurationEl) thinkDurationEl.innerText = '';
        if (progressController) progressController.stop();
    };

    // === Fetch & Stream ===
    let hasStartedTimer = false;
    let forcedEndThinking = false;
    let gmailPreviewRendered = false;
    let gmailActionsRendered = false;
    const attachmentsPayload = isResume ? [] : (chatMessages[chatMessages.length - 1]?.attachments || []);
    const requestSkillType = (!isResume && isAgentMode && selectedSkillType) ? selectedSkillType : '';
    const forceRegeneratePdf = !!window._forceRegeneratePdf;
    window._forceRegeneratePdf = false;
    if (requestSkillType) clearSelectedSkillType();

    try {
        const response = await fetch('/api/chat', {
            method: 'POST',
            credentials: 'same-origin',
            headers: authenticatedHeaders({ 'Content-Type': 'application/json' }),
            body: JSON.stringify({
                chat_id: currentChatId,
                user_id: currentUserId,
                message: isResume ? '' : chatMessages[chatMessages.length - 1]?.content || '',
                messages: chatMessages,
                attachments: attachmentsPayload,
                think_mode: supportsThinkMode && (isAgentMode ? true : isThinkMode),
                web_mode: isAgentMode ? false : isWebMode,
                is_resume: isResume,
                agent_mode: isAgentMode,
                user_timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || '',
                browser_language: navigator.language || navigator.userLanguage || '',
                regenerate_pdf: forceRegeneratePdf,
                skill_type: requestSkillType,
            }),
            signal: currentAbortController.signal,
        });
        if (!response.ok || !response.body) {
            throw new Error(`CHAT_HTTP_${response.status || 'NO_BODY'}`);
        }

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let sseBuffer = '';
        let streamCompleted = false;

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            sseBuffer += decoder.decode(value, { stream: true });
            const lines = sseBuffer.split('\n');
            sseBuffer = lines.pop() || '';

            for (const line of lines) {
                if (!line.startsWith('data: ')) continue;
                const dataStr = line.slice(6);

                if (dataStr === '[DONE]') {
                    streamCompleted = true;
                    isGenerating = false;
                    currentAbortController = null;
                    submitBtn.className = 'submit-btn';
                    submitBtn.innerHTML = '<i class="fa-solid fa-arrow-up"></i>';
                    removeImageGenerationLoader();
                    if (thinkTimerInterval) clearInterval(thinkTimerInterval);
                    if (progressController) progressController.stop();

                    // Reset any pending confirm buttons that might be stuck spinning
                    document.querySelectorAll('.gmail-confirm-btn').forEach(btn => {
                        if (btn.innerHTML.includes('fa-spinner') || btn.innerHTML.includes('Sending...')) {
                            btn.innerHTML = '<i class="fa-solid fa-check"></i> Sent';
                            btn.style.background = '#16a34a';
                        }
                    });

                    // Finalize think header
                    if (thinkWrapper) {
                        if (imageOnlyProgressStream) {
                            thinkWrapper.style.display = 'none';
                            thinkWrapper.classList.remove('in-progress');
                        } else {
                            if (!thinkStartTime) thinkStartTime = Date.now();
                            const elapsed = ((Date.now() - thinkStartTime) / 1000).toFixed(0);
                            thinkHeader.querySelector('.think-icon').innerHTML = '<i class="fa-solid fa-circle-check"></i>';

                            if (passiveProgressMode) {
                                thinkWrapper.style.display = 'none';
                            } else if (effectiveThinkMode && !effectiveWebMode && frontendThinkAccum.trim().length === 0) {
                                thinkWrapper.style.display = 'none';
                            } else {
                                if (effectiveThinkMode) {
                                    thinkHeader.querySelector('.think-label').innerText = `THOUGHT FOR ${elapsed} s`;
                                } else {
                                    thinkHeader.querySelector('.think-label').innerText = `SEARCHED FOR ${elapsed} s`;
                                }

                                thinkDurationEl.innerText = '';
                                thinkWrapper.classList.add('done');
                            }
                        }
                    }

                    const msgStore = makeAssistantMsgStore();
                    if (isResume && resumeIndex !== null) {
                        chatMessages[resumeIndex] = msgStore;
                        createActionButtons(assistantWrapper, resumeIndex, 0, true, rawAccumText);
                    } else {
                        chatMessages.push(msgStore);
                        createActionButtons(assistantWrapper, chatMessages.length - 1, 0, true, rawAccumText);
                    }

                    // Apply syntax highlighting + math rendering after streaming completes
                    scheduleAnswerRender(true);
                    scheduleThinkRender(true);
                    applyRichFormatting(contentBox);
                    if (thinkContent) applyRichFormatting(thinkContent);

                    return;
                }

                try {
                    const data = JSON.parse(dataStr);

                    // Chat ID
                    if (data.chat_id) {
                        if (!currentChatId) { currentChatId = data.chat_id; loadHistory(); }
                        continue;
                    }

                    // Search sources → render as cards
                    if (data.sources && thinkSourcesBar) {
                        currentSources = data.sources;
                        thinkSourcesBar.style.display = 'block';
                        const srcs = data.sources;
                        thinkSourcesBar.innerHTML = '';

                        // Header
                        const headerDiv = document.createElement('div');
                        headerDiv.className = 'sources-header';
                        headerDiv.innerHTML = srcs.length
                            ? `<i class="fa-solid fa-magnifying-glass"></i> Read ${srcs.length} sources`
                            : `<i class="fa-solid fa-circle-exclamation"></i> No live sources found`;
                        thinkSourcesBar.appendChild(headerDiv);

                        // Scrollable cards container
                        const scrollContainer = document.createElement('div');
                        scrollContainer.className = 'sources-scroll';

                        srcs.forEach(s => {
                            const sourceUrl = safeExternalUrl(s.url);
                            if (!sourceUrl) return;
                            const card = document.createElement('a');
                            card.className = 'source-card';
                            card.href = sourceUrl;
                            card.target = '_blank';
                            card.rel = 'noopener noreferrer';

                            let domain = '';
                            try { domain = new URL(sourceUrl).hostname.replace('www.',''); } catch {}
                            const favUrl = getFavicon(sourceUrl);

                            card.innerHTML = `
                                ${favUrl ? `<img class="source-card-favicon" src="${escapeAttr(favUrl)}" alt="">` : ''}
                                <div class="source-card-text">
                                    <span class="source-card-title">${escapeHtml(s.title || domain)}</span>
                                    <span class="source-card-domain">${escapeHtml(domain)}</span>
                                </div>
                            `;
                            scrollContainer.appendChild(card);
                        });

                        thinkSourcesBar.appendChild(scrollContainer);
                        scrollToBottom();
                        continue;
                    }

                    // Phase Events
                    if (data.status === 'searching' || data.status === 'answering' || data.think_start) {
                        if (!thinkStartTime && thinkWrapper) {
                            thinkStartTime = Date.now();
                            if (!hasStartedTimer) {
                                thinkTimerInterval = setInterval(() => {
                                    const s = ((Date.now() - thinkStartTime) / 1000).toFixed(0);
                                    if (thinkDurationEl) thinkDurationEl.innerText = `${s}s`;
                                }, 1000);
                                hasStartedTimer = true;
                            }
                        }
                    }

                    // Status events
                    if (data.status === 'parsing_pdf' && thinkHeader) {
                        thinkWrapper.style.display = 'block';
                        thinkHeader.querySelector('.think-label').innerText = '📄 Parsing PDF document...';
                        thinkHeader.querySelector('.think-icon').innerHTML = '<i class="fa-solid fa-file-pdf fa-spin"></i>';
                        if (!hasStartedTimer) {
                            thinkStartTime = Date.now();
                            thinkTimerInterval = setInterval(() => {
                                const s = ((Date.now() - thinkStartTime) / 1000).toFixed(0);
                                if (thinkDurationEl) thinkDurationEl.innerText = `${s}s`;
                            }, 1000);
                            hasStartedTimer = true;
                        }
                        continue;
                    }
                    if (data.status === 'parsing_data' && thinkHeader) {
                        thinkWrapper.style.display = 'block';
                        thinkHeader.querySelector('.think-label').innerText = 'Parsing financial data...';
                        thinkHeader.querySelector('.think-icon').innerHTML = '<i class="fa-solid fa-file-csv fa-spin"></i>';
                        if (!hasStartedTimer) {
                            thinkStartTime = Date.now();
                            thinkTimerInterval = setInterval(() => {
                                const s = ((Date.now() - thinkStartTime) / 1000).toFixed(0);
                                if (thinkDurationEl) thinkDurationEl.innerText = `${s}s`;
                            }, 1000);
                            hasStartedTimer = true;
                        }
                        continue;
                    }
                    if (data.status === 'preparing_image' && thinkHeader) {
                        thinkWrapper.style.display = 'block';
                        thinkHeader.querySelector('.think-label').innerText = 'Preparing image...';
                        thinkHeader.querySelector('.think-icon').innerHTML = '<i class="fa-regular fa-image fa-spin"></i>';
                        if (!hasStartedTimer) {
                            thinkStartTime = Date.now();
                            thinkTimerInterval = setInterval(() => {
                                const s = ((Date.now() - thinkStartTime) / 1000).toFixed(0);
                                if (thinkDurationEl) thinkDurationEl.innerText = `${s}s`;
                            }, 1000);
                            hasStartedTimer = true;
                        }
                        continue;
                    }
                    if (data.status === 'analyzing_image' && thinkHeader) {
                        thinkWrapper.style.display = 'block';
                        thinkHeader.querySelector('.think-label').innerText = 'Reading image details...';
                        thinkHeader.querySelector('.think-icon').innerHTML = '<i class="fa-regular fa-image fa-spin"></i>';
                        if (!hasStartedTimer) {
                            thinkStartTime = Date.now();
                            thinkTimerInterval = setInterval(() => {
                                const s = ((Date.now() - thinkStartTime) / 1000).toFixed(0);
                                if (thinkDurationEl) thinkDurationEl.innerText = `${s}s`;
                            }, 1000);
                            hasStartedTimer = true;
                        }
                        continue;
                    }
                    if (data.status === 'generating_image') {
                        imageOnlyProgressStream = true;
                        hideImageThinkPanel();
                        showImageGenerationLoader();
                        continue;
                    }
                    if (data.status === 'creating_skill_file' && thinkHeader) {
                        const typeLabel = String(data.file_type || 'file').toUpperCase();
                        thinkWrapper.style.display = 'block';
                        thinkHeader.querySelector('.think-label').innerText = `${tUi('skillCreatingFile', 'Creating file')} ${typeLabel}...`;
                        thinkHeader.querySelector('.think-icon').innerHTML = '<i class="fa-solid fa-file-circle-plus fa-spin"></i>';
                        if (!hasStartedTimer) {
                            thinkStartTime = Date.now();
                            thinkTimerInterval = setInterval(() => {
                                const s = ((Date.now() - thinkStartTime) / 1000).toFixed(0);
                                if (thinkDurationEl) thinkDurationEl.innerText = `${s}s`;
                            }, 1000);
                            hasStartedTimer = true;
                        }
                        continue;
                    }
                    if (data.status === 'searching' && thinkHeader) {
                        thinkHeader.querySelector('.think-label').innerText = 'Searching the web...';
                        thinkHeader.querySelector('.think-icon').innerHTML = '<i class="fa-solid fa-globe fa-spin"></i>';
                        continue;
                    }
                    if (data.status === 'answering' && thinkHeader) {
                        if (isThinkMode) {
                            thinkHeader.querySelector('.think-label').innerText = 'Thinking...';
                            thinkHeader.querySelector('.think-icon').innerHTML = '<i class="fa-solid fa-atom fa-spin"></i>';
                        } else {
                            thinkHeader.querySelector('.think-label').innerText = 'Synthesizing search results...';
                            thinkHeader.querySelector('.think-icon').innerHTML = '<i class="fa-solid fa-pen-fancy fa-spin"></i>';
                        }
                        continue;
                    }
                    if (data.status === 'model_starting' && thinkHeader) {
                        const label = isAgentMode ? 'Composing workspace response...' : (isWebMode ? 'Writing sourced answer...' : 'Writing answer...');
                        thinkHeader.querySelector('.think-label').innerText = label;
                        thinkHeader.querySelector('.think-icon').innerHTML = '<i class="fa-solid fa-sparkles fa-spin"></i>';
                        continue;
                    }

                    if (data.think_start) {
                        if (!rawAccumText.includes('<think>')) rawAccumText += '<think>\n';
                        continue;
                    }

                    if (data.think_end) {
                        if (!rawAccumText.includes('</think>')) rawAccumText += '\n</think>\n';
                        forcedEndThinking = true;
                        submitBtn.className = 'submit-btn answering-state';
                        submitBtn.innerHTML = '<i class="fa-solid fa-pause"></i>';
                        continue;
                    }
                    // === TEXT CHUNKS ===
                    if (data.text !== undefined) {
                        removeImageGenerationLoader();
                        let textChunk = data.text;
                        rawAccumText += textChunk;

                        if (passiveProgressMode && thinkWrapper && !passiveProgressHidden) {
                            passiveProgressHidden = true;
                            thinkWrapper.classList.remove('in-progress');
                            thinkWrapper.style.display = 'none';
                            if (progressController) progressController.stop();
                        }

                        if (effectiveThinkMode) {
                            if (data.thinking) {
                                frontendThinkAccum += textChunk;
                                // Auto-expand think wrapper when content starts arriving
                                if (thinkWrapper && thinkWrapper.classList.contains('collapsed') && frontendThinkAccum.trim().length > 0) {
                                    thinkWrapper.classList.remove('collapsed');
                                }
                            } else {
                                frontendAnswerAccum += textChunk;
                            }

                            if (thinkContent) {
                                scheduleThinkRender();
                                if (frontendThinkAccum.trim().length > 0 && thinkWrapper.style.display === 'none') {
                                    thinkWrapper.style.display = 'block';
                                }
                            }
                            scheduleAnswerRender();

                            if (!thinkStartTime && thinkWrapper && (data.think_start || data.thinking || frontendThinkAccum.length > 0)) {
                                if (!hasStartedTimer) {
                                    thinkStartTime = Date.now();
                                    thinkTimerInterval = setInterval(() => {
                                        const s = ((Date.now() - thinkStartTime) / 1000).toFixed(0);
                                        if (thinkDurationEl) thinkDurationEl.innerText = `${s}s`;
                                    }, 1000);
                                    hasStartedTimer = true;
                                }
                            }
                        } else {
                            frontendAnswerAccum += textChunk;
                            scheduleAnswerRender();
                        }

                        const gmailPreviewPayload = decodeGmailPreviewPayload(frontendAnswerAccum);
                        if (gmailPreviewPayload && !gmailPreviewRendered) {
                            frontendAnswerAccum = stripGmailPreviewPayload(frontendAnswerAccum);
                            rawAccumText = stripGmailPreviewPayload(rawAccumText);
                            scheduleAnswerRender(true);
                            const previewCard = createGmailPreviewCard(gmailPreviewPayload);
                            if (previewCard) assistantWrapper.appendChild(previewCard);
                            gmailPreviewRendered = true;
                        }

                        if (frontendAnswerAccum.includes('[GMAIL_CONFIRM_PENDING]') && !gmailActionsRendered) {
                            frontendAnswerAccum = frontendAnswerAccum.replace('[GMAIL_CONFIRM_PENDING]', '');
                            rawAccumText = rawAccumText.replace('[GMAIL_CONFIRM_PENDING]', '');
                            scheduleAnswerRender(true);
                            assistantWrapper.appendChild(createGmailActionCard());
                            gmailActionsRendered = true;
                        }
                        scrollToBottom();
                    }

                    if (data.file_ready && data.file_url) {
                        const fileName = data.file_name || 'generated-file';
                        const fileType = data.file_type || '';
                        generatedFile = {
                            file_url: data.file_url,
                            file_name: fileName,
                            file_type: fileType,
                            content_type: data.content_type || getMimeTypeForFilename(fileName),
                            pdf_url: fileType === 'pdf' ? `/api/download_pdf/${fileName}` : ''
                        };
                        if (fileType === 'pdf') {
                            generatedPdf = { pdf_url: generatedFile.pdf_url, pdf_name: fileName };
                        }
                        invalidateRecentFilesCache();
                        const fileCard = createGeneratedFileCard(data.file_url, fileName, fileType, tUi('generatedFileTitle', 'Generated File'));
                        assistantWrapper.appendChild(fileCard);
                        scrollToBottom();
                        continue;
                    }

                    // === PDF READY — show download card ===
                    if (data.pdf_ready && data.pdf_url) {
                        const pdfName = data.pdf_name || 'PepperReport.pdf';
                        generatedPdf = { pdf_url: data.pdf_url, pdf_name: pdfName };
                        invalidateRecentFilesCache();
                        const dlCard = createPdfDownloadCard(data.pdf_url, pdfName);
                        assistantWrapper.appendChild(dlCard);
                        scrollToBottom();
                        continue;
                    }

                    if (data.image_ready && data.image_url) {
                        removeImageGenerationLoader();
                        const imageName = data.image_name || 'generated-image.png';
                        generatedImage = { image_url: data.image_url, image_name: imageName };
                        invalidateRecentFilesCache();
                        const imageCard = createImageResultCard(data.image_url, imageName, 'Generated Image');
                        assistantWrapper.appendChild(imageCard);
                        scrollToBottom();
                        continue;
                    }

                } catch (e) { /* partial JSON */ }
            }
        }
        if (!streamCompleted) {
            throw new Error('STREAM_ENDED_WITHOUT_DONE');
        }
    } catch (e) {
        removeImageGenerationLoader();
        if (progressController) progressController.stop();
        if (answerRenderTimer) {
            clearTimeout(answerRenderTimer);
            answerRenderTimer = null;
            renderAnswerNow();
        }
        if (thinkRenderTimer) {
            clearTimeout(thinkRenderTimer);
            thinkRenderTimer = null;
            renderThinkNow();
        }
        const streamEndedEarly = e && e.message === 'STREAM_ENDED_WITHOUT_DONE';
        const interrupted = (e && e.name === 'AbortError') || streamEndedEarly;
        if (interrupted) {
            if (thinkTimerInterval) clearInterval(thinkTimerInterval);
            if (thinkHeader && !forcedEndThinking && !imageOnlyProgressStream) {
                const durationStr = thinkDurationEl ? thinkDurationEl.innerText : '';
                thinkHeader.querySelector('.think-icon').innerHTML = streamEndedEarly
                    ? '<i class="fa-solid fa-triangle-exclamation"></i>'
                    : '<i class="fa-solid fa-pause"></i>';
                thinkHeader.querySelector('.think-label').innerText = streamEndedEarly
                    ? 'Stream interrupted'
                    : 'Thinking Paused';
                if (durationStr) thinkDurationEl.innerText = durationStr;
            }

            const wrap = document.createElement('div');
            wrap.className = 'markdown-content';
            wrap.innerHTML = streamEndedEarly
                ? '<br><em style="color:var(--primary-dim); font-size: 0.9em;"><i class="fa-solid fa-triangle-exclamation"></i> Connection interrupted. Click resume to continue from here.</em>'
                : '<br><em style="color:var(--primary-dim); font-size: 0.9em;"><i class="fa-solid fa-pause"></i> Generation paused by user</em>';
            contentBox.appendChild(wrap);

            const msgStore = makeAssistantMsgStore();
            let savedIndex = null;
            if (isResume && resumeIndex !== null) {
                chatMessages[resumeIndex] = msgStore;
                savedIndex = resumeIndex;
            } else {
                chatMessages.push(msgStore);
                savedIndex = chatMessages.length - 1;
            }
            createActionButtons(assistantWrapper, savedIndex, 0, true, rawAccumText);

            isPaused = true;
            pausedMsgIndex = savedIndex;
        } else {
            console.error("Chat stream failed", e);
            if (rawAccumText && rawAccumText.trim()) {
                const wrap = document.createElement('div');
                wrap.className = 'markdown-content';
                wrap.innerHTML = '<br><em style="color:var(--primary-dim); font-size: 0.9em;"><i class="fa-solid fa-triangle-exclamation"></i> Response interrupted. Click resume to continue.</em>';
                contentBox.appendChild(wrap);
                const msgStore = makeAssistantMsgStore();
                let savedIndex = null;
                if (isResume && resumeIndex !== null) {
                    chatMessages[resumeIndex] = msgStore;
                    savedIndex = resumeIndex;
                } else {
                    chatMessages.push(msgStore);
                    savedIndex = chatMessages.length - 1;
                }
                createActionButtons(assistantWrapper, savedIndex, 0, true, rawAccumText);
                isPaused = true;
                pausedMsgIndex = savedIndex;
            } else {
                contentBox.innerText = "Error connecting to server.";
            }
        }
        isGenerating = false;
        currentAbortController = null;
        if (isPaused) {
            submitBtn.className = 'submit-btn paused-state';
            submitBtn.innerHTML = '<i class="fa-solid fa-play"></i>';
        } else {
            submitBtn.className = 'submit-btn';
            submitBtn.innerHTML = '<i class="fa-solid fa-arrow-up"></i>';
        }

        // Reset any pending confirm buttons that might be stuck spinning
        document.querySelectorAll('.gmail-confirm-btn').forEach(btn => {
            if (btn.innerHTML.includes('fa-spinner') || btn.innerHTML.includes('Sending...')) {
                btn.innerHTML = '<i class="fa-solid fa-check"></i> Sent';
                btn.style.background = '#16a34a';
            }
        });
    }
}

// ============ Attachments Handle ============
let pendingFiles = [];
let pendingPreviewObjectUrls = [];
const fileInput = document.getElementById('fileInput');
const uploadBtn = document.getElementById('uploadBtn');
const attachmentsPreview = document.getElementById('attachmentsPreview');

function formatFileSize(bytes) {
    const size = Number(bytes || 0);
    if (size < 1024) return `${size} B`;
    if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
    return `${(size / 1024 / 1024).toFixed(2)} MB`;
}

function getAttachmentIcon(name = '', type = '') {
    const lower = String(name || '').toLowerCase();
    const normalizedType = String(type || '').toLowerCase();
    if (normalizedType.startsWith('image/')) return { iconClass: 'fa-file-image', iconColor: '#4c8de2' };
    if (normalizedType === 'application/pdf' || lower.endsWith('.pdf')) return { iconClass: 'fa-file-pdf', iconColor: '#e2574c' };
    if (lower.endsWith('.docx') || lower.endsWith('.doc')) return { iconClass: 'fa-file-word', iconColor: '#2563eb' };
    if (lower.endsWith('.pptx') || lower.endsWith('.ppt')) return { iconClass: 'fa-file-powerpoint', iconColor: '#ea580c' };
    if (lower.endsWith('.csv') || lower.endsWith('.tsv')) return { iconClass: 'fa-file-csv', iconColor: '#16a34a' };
    if (lower.endsWith('.xlsx') || lower.endsWith('.xls')) return { iconClass: 'fa-file-excel', iconColor: '#15803d' };
    if (lower.endsWith('.json') || lower.endsWith('.jsonl')) return { iconClass: 'fa-file-code', iconColor: '#7c3aed' };
    return { iconClass: 'fa-file-lines', iconColor: 'var(--primary-dim)' };
}

function isImageFile(file) {
    if (!file) return false;
    const type = String(file.type || '').toLowerCase();
    const name = String(file.name || '').toLowerCase();
    return type.startsWith('image/')
        || /\.(png|jpe?g|webp|gif|bmp|svg)$/i.test(name)
        || name.startsWith('pasted-image-');
}

function isImageAttachment(att) {
    const name = String(att?.original_name || att?.saved_path || '').toLowerCase();
    return String(att?.content_type || '').toLowerCase().startsWith('image/')
        || /\.(png|jpe?g|webp|gif|bmp|svg)$/i.test(name);
}

function isPdfAttachment(att) {
    const name = String(att?.original_name || att?.saved_path || '').toLowerCase();
    return String(att?.content_type || '').toLowerCase() === 'application/pdf'
        || name.endsWith('.pdf');
}

function isDocumentAttachment(att) {
    const name = String(att?.original_name || att?.saved_path || '').toLowerCase();
    return /\.(docx?|pptx?|xlsx?)$/i.test(name);
}

function isRecentFileAttachment(att) {
    return isImageAttachment(att) || isPdfAttachment(att) || isDocumentAttachment(att);
}

function getMimeTypeForFilename(name = '', fallback = '') {
    const lower = String(name || '').toLowerCase();
    if (lower.endsWith('.pdf')) return 'application/pdf';
    if (lower.endsWith('.docx')) return 'application/vnd.openxmlformats-officedocument.wordprocessingml.document';
    if (lower.endsWith('.xlsx')) return 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet';
    if (lower.endsWith('.pptx')) return 'application/vnd.openxmlformats-officedocument.presentationml.presentation';
    if (lower.endsWith('.doc')) return 'application/msword';
    if (lower.endsWith('.xls')) return 'application/vnd.ms-excel';
    if (lower.endsWith('.ppt')) return 'application/vnd.ms-powerpoint';
    if (lower.endsWith('.png')) return 'image/png';
    if (lower.endsWith('.jpg') || lower.endsWith('.jpeg')) return 'image/jpeg';
    if (lower.endsWith('.webp')) return 'image/webp';
    if (lower.endsWith('.gif')) return 'image/gif';
    if (lower.endsWith('.svg')) return 'image/svg+xml';
    if (fallback) return fallback;
    return 'application/octet-stream';
}

function getRecentFileKind(name = '', contentType = '') {
    const lower = String(name || '').toLowerCase();
    const type = String(contentType || '').toLowerCase();
    if (type.startsWith('image/') || /\.(png|jpe?g|webp|gif|bmp|svg)$/i.test(lower)) return 'image';
    if (type === 'application/pdf' || lower.endsWith('.pdf')) return 'pdf';
    if (/\.(docx?|pptx?|xlsx?)$/i.test(lower)) return lower.split('.').pop();
    return 'file';
}

function getDefaultImageAnalysisPrompt() {
    const lang = getPreferredLanguage();
    if (lang === 'ms') return 'Sila analisis imej yang saya muat naik, terangkan kandungan yang kelihatan, ekstrak teks, dan nyatakan butiran penting.';
    return 'Please analyze the uploaded image(s), describe the visible content, extract any text, and point out important details.';
}

uploadBtn.addEventListener('click', () => {
    if (!currentUserId && isAgentMode) {
        showGuestLoginPrompt(true);
        return;
    }
    fileInput.click();
});

const imgUploadBtn = document.getElementById('imgUploadBtn');
const imageInput = document.getElementById('imageInput');
const uploadMenuShell = document.getElementById('uploadMenuShell');
const uploadDropdown = document.getElementById('uploadDropdown');
const recentFilesDropdown = document.getElementById('recentFilesDropdown');
const skillsDropdown = document.getElementById('skillsDropdown');
const uploadFileMenuItem = document.getElementById('uploadFileMenuItem');
const recentFilesMenuItem = document.getElementById('recentFilesMenuItem');
const skillsMenuItem = document.getElementById('skillsMenuItem');
const mobileUploadSheet = document.getElementById('mobileUploadSheet');
const mobileUploadSheetCloseBtn = document.getElementById('mobileUploadSheetCloseBtn');
const mobileUploadActionsView = document.getElementById('mobileUploadActionsView');
const mobileUploadRecentView = document.getElementById('mobileUploadRecentView');
const mobileUploadSkillsView = document.getElementById('mobileUploadSkillsView');
const mobileUploadRecentList = document.getElementById('mobileUploadRecentList');
let mobileUploadSheetReturnFocus = null;
let recentFileItemsCache = null;
let recentFileItemsPromise = null;

function isMobileUploadSheetOpen() {
    return Boolean(mobileUploadSheet?.open);
}

function setMobileUploadView(view = 'actions') {
    if (!mobileUploadSheet) return;
    mobileUploadActionsView?.classList.toggle('hidden', view !== 'actions');
    mobileUploadRecentView?.classList.toggle('hidden', view !== 'recent');
    mobileUploadSkillsView?.classList.toggle('hidden', view !== 'skills');
}

function closeMobileUploadSheet({ restoreFocus = false } = {}) {
    if (!isMobileUploadSheetOpen()) return;
    mobileUploadSheet.close();
    if (restoreFocus) {
        window.setTimeout(() => {
            (mobileUploadSheetReturnFocus || imgUploadBtn)?.focus?.({ preventScroll: true });
        }, 0);
    }
}

function openMobileUploadSheet() {
    if (!mobileUploadSheet || !isMobileModePickerLayout() || isMobileUploadSheetOpen()) return;
    mobileUploadSheetReturnFocus = document.activeElement instanceof HTMLElement
        ? document.activeElement
        : imgUploadBtn;
    if (document.activeElement instanceof HTMLElement) document.activeElement.blur();
    uploadMenuShell?.classList.remove('open');
    setRecentFilesOpen(false);
    setSkillsMenuOpen(false);
    setMobileUploadView('actions');
    mobileUploadSheet.classList.toggle('is-agent-mode', Boolean(isAgentMode));
    imgUploadBtn?.setAttribute('aria-expanded', 'true');
    document.body.classList.add('mobile-upload-sheet-open');
    mobileUploadSheet.showModal();
    window.setTimeout(() => mobileUploadSheetCloseBtn?.focus({ preventScroll: true }), 0);
}

async function addMobileRecentFile(item) {
    if (!item?.url) return;
    try {
        const res = await fetch(item.url, { cache: 'no-store' });
        if (!res.ok) throw new Error(`RECENT_FILE_HTTP_${res.status}`);
        const blob = await res.blob();
        const filename = item.sourceName || item.name || (item.kind === 'pdf' ? 'document.pdf' : 'image.png');
        const type = getMimeTypeForFilename(filename, blob.type || item.contentType || '');
        pendingFiles.push(new File([blob], filename, { type }));
        renderAttachmentsPreview();
        closeMobileUploadSheet();
    } catch (err) {
        console.error('Failed to add recent file', err);
        showToast(tUi('recentFilesLoadError', 'Unable to load recent files'), true);
    }
}

async function renderMobileRecentFiles() {
    if (!mobileUploadRecentList) return;
    mobileUploadRecentList.innerHTML = `<div class="mobile-upload-sheet__state">${escapeHtml(tUi('recentFilesLoading', 'Loading...'))}</div>`;
    try {
        const allItems = await fetchRecentFileItems();
        const items = (isAgentMode ? allItems : allItems.filter(item => item.kind === 'image')).slice(0, 16);
        if (!items.length) {
            mobileUploadRecentList.innerHTML = `<div class="mobile-upload-sheet__state">${escapeHtml(tUi('recentFilesEmpty', 'No recent files'))}</div>`;
            return;
        }
        mobileUploadRecentList.innerHTML = '';
        items.forEach(item => {
            const icon = getAttachmentIcon(item.name, item.contentType || '');
            const button = document.createElement('button');
            button.className = 'mobile-upload-sheet__recent-item';
            button.type = 'button';
            button.setAttribute('role', 'menuitem');
            button.innerHTML = `
                <span class="mobile-upload-sheet__recent-icon" style="color:${escapeAttr(icon.iconColor)}"><i class="fa-solid ${escapeAttr(icon.iconClass)}" aria-hidden="true"></i></span>
                <span class="mobile-upload-sheet__recent-name">${escapeHtml(item.name)}</span>
            `;
            button.addEventListener('click', () => addMobileRecentFile(item));
            mobileUploadRecentList.appendChild(button);
        });
    } catch (err) {
        console.error('Failed to render mobile recent files', err);
        mobileUploadRecentList.innerHTML = `<div class="mobile-upload-sheet__state">${escapeHtml(tUi('recentFilesLoadError', 'Unable to load recent files'))}</div>`;
    }
}

function invalidateRecentFilesCache() {
    recentFileItemsCache = null;
    recentFileItemsPromise = null;
    if (uploadMenuShell?.classList.contains('recent-open')) {
        renderRecentFilesMenu();
    }
}

function setUploadMenuOpen(open) {
    if (!uploadMenuShell || !imgUploadBtn) return;
    if (isMobileModePickerLayout()) {
        if (open) openMobileUploadSheet();
        else closeMobileUploadSheet();
        return;
    }
    uploadMenuShell.classList.toggle('open', open);
    if (!open) {
        setRecentFilesOpen(false);
        setSkillsMenuOpen(false);
    }
    imgUploadBtn.setAttribute('aria-expanded', open ? 'true' : 'false');
}

function setRecentFilesOpen(open) {
    if (!uploadMenuShell || !recentFilesMenuItem) return;
    uploadMenuShell.classList.toggle('recent-open', open);
    recentFilesMenuItem.classList.toggle('recent-active', open);
    recentFilesMenuItem.setAttribute('aria-expanded', open ? 'true' : 'false');
    if (open) setSkillsMenuOpen(false);
    if (open) renderRecentFilesMenu();
}

function setSkillsMenuOpen(open) {
    if (!uploadMenuShell || !skillsMenuItem) return;
    const nextOpen = open && uploadMenuShell.classList.contains('agent-mode');
    uploadMenuShell.classList.toggle('skills-open', nextOpen);
    skillsMenuItem.classList.toggle('skills-active', nextOpen);
    skillsMenuItem.setAttribute('aria-expanded', nextOpen ? 'true' : 'false');
    if (nextOpen) setRecentFilesOpen(false);
}

function updateSelectedSkillUi() {
    document.querySelectorAll('.skill-file-item[data-skill]').forEach(btn => {
        const active = btn.dataset.skill === selectedSkillType;
        btn.classList.toggle('is-selected', active);
        btn.setAttribute('aria-checked', active ? 'true' : 'false');
    });
    if (!userInput) return;
    if (selectedSkillType) {
        const label = selectedSkillType.toUpperCase();
        userInput.placeholder = tUi('skillSelectedPlaceholder', '{type} skill selected. Describe what to create.').replace('{type}', label);
    } else {
        const compactPlaceholder = window.innerWidth <= 760;
        userInput.placeholder = compactPlaceholder
            ? tUi('mobilePlaceholder', tUi('placeholder', 'What do you want to know?'))
            : tUi('placeholder', 'What do you want to know?');
    }
}

function setSelectedSkillType(skillType) {
    selectedSkillType = (skillType || '').toLowerCase();
    updateSelectedSkillUi();
}

function clearSelectedSkillType() {
    if (!selectedSkillType) return;
    selectedSkillType = '';
    updateSelectedSkillUi();
}

function openUploadPickerFromMenu() {
    setUploadMenuOpen(false);
    if (connectorsContainer) connectorsContainer.classList.remove('open');
    setModeDropdownOpen(false);
    if (!currentUserId) {
        showGuestLoginPrompt(false);
        return;
    }
    fileInput.click();
}

function getAttachmentAssetUrl(att) {
    if (!att) return '';
    if (att.url) return att.url;
    if (att.saved_path) {
        const parts = String(att.saved_path).replace(/\\/g, '/').split('/');
        const fname = parts[parts.length - 1];
        if (fname) return '/uploads/' + fname;
    }
    if (att.file_id) {
        const original = String(att.original_name || '');
        const ext = original.includes('.') ? original.slice(original.lastIndexOf('.')) : '.png';
        return '/uploads/' + att.file_id + ext;
    }
    return '';
}

function getFriendlyRecentFileName(name = '', fallback = '') {
    const clean = String(name || '').trim();
    if (clean) return clean;
    return fallback || tUi('recentFilesUnnamed', 'Untitled');
}

async function fetchRecentFileItems() {
    if (recentFileItemsCache) return recentFileItemsCache;
    if (recentFileItemsPromise) return recentFileItemsPromise;
    recentFileItemsPromise = (async () => {
        if (!currentUserId) return [];
        const historyRes = await fetch('/api/history', {
            cache: 'no-store',
            headers: authenticatedHeaders(),
        });
        const historyData = await historyRes.json();
        const chats = Array.isArray(historyData.chats) ? historyData.chats.slice(0, 24) : [];
        const items = [];
        const seen = new Set();
        const maxCollectedItems = 32;

        const addItem = (item) => {
            if (!item.url) return;
            const key = normalizeAssetUrl(item.url);
            if (!key || seen.has(key)) return;
            seen.add(key);
            items.push({ ...item, url: key });
        };

        const collectMessages = (messages) => {
            for (const msg of [...messages].reverse()) {
                if (items.length >= maxCollectedItems) break;
                if (msg.generated_image_url) {
                    addItem({
                        url: msg.generated_image_url,
                        name: getFriendlyRecentFileName(msg.generated_image_name, 'image.png'),
                        sourceName: msg.generated_image_name || 'generated-image.png',
                        contentType: 'image/png',
                        kind: 'image',
                        generated: true,
                    });
                }
                if (msg.pdf_url) {
                    addItem({
                        url: msg.pdf_url,
                        name: getFriendlyRecentFileName(msg.pdf_name, 'document.pdf'),
                        sourceName: msg.pdf_name || 'document.pdf',
                        contentType: 'application/pdf',
                        kind: 'pdf',
                        generated: true,
                    });
                }
                if (msg.generated_file_url && msg.generated_file_type !== 'pdf') {
                    const generatedFileName = msg.generated_file_name || 'generated-file';
                    const generatedKind = getRecentFileKind(generatedFileName, msg.generated_file_content_type || '');
                    addItem({
                        url: msg.generated_file_url,
                        name: getFriendlyRecentFileName(generatedFileName, 'generated-file'),
                        sourceName: generatedFileName,
                        contentType: msg.generated_file_content_type || getMimeTypeForFilename(generatedFileName),
                        kind: generatedKind,
                        generated: true,
                    });
                }
                for (const att of (msg.attachments || [])) {
                    if (items.length >= maxCollectedItems) break;
                    if (!isRecentFileAttachment(att)) continue;
                    const url = getAttachmentAssetUrl(att);
                    const kind = getRecentFileKind(att.original_name || att.saved_path, att.content_type || '');
                    const fallbackName = kind === 'pdf'
                        ? 'document.pdf'
                        : (kind === 'image' ? 'image.png' : `document.${kind === 'file' ? 'bin' : kind}`);
                    addItem({
                        url,
                        name: getFriendlyRecentFileName(att.original_name, fallbackName),
                        sourceName: att.original_name || fallbackName,
                        contentType: getMimeTypeForFilename(att.original_name || att.saved_path, att.content_type || ''),
                        kind,
                        generated: false,
                    });
                }
            }
        };

        // Fetch a small batch in parallel, then process each response in the
        // original history order. This removes the long 24-request waterfall
        // without changing ordering or de-duplication semantics.
        const recentFileConcurrency = 5;
        for (let offset = 0; offset < chats.length && items.length < maxCollectedItems; offset += recentFileConcurrency) {
            const batch = chats.slice(offset, offset + recentFileConcurrency);
            const batchMessages = await Promise.all(batch.map(async chat => {
                const chatId = chat?._id;
                if (!chatId) return [];
                try {
                    const res = await fetch(`/api/history/${chatId}`, {
                        cache: 'no-store',
                        headers: authenticatedHeaders(),
                    });
                    if (!res.ok) throw new Error(`History request failed with status ${res.status}`);
                    const data = await res.json();
                    return Array.isArray(data.chat?.messages) ? data.chat.messages : [];
                } catch (err) {
                    console.warn('Failed to load recent files from chat', chatId, err);
                    return [];
                }
            }));
            for (const messages of batchMessages) {
                collectMessages(messages);
                if (items.length >= maxCollectedItems) break;
            }
        }
        recentFileItemsCache = items;
        return items;
    })().finally(() => {
        recentFileItemsPromise = null;
    });
    return recentFileItemsPromise;
}

async function renderRecentFilesMenu() {
    if (!recentFilesDropdown) return;
    if (!currentUserId) {
        recentFilesDropdown.innerHTML = recentFilesStateMarkup('recentFilesLoginRequired', 'Please log in');
        return;
    }
    recentFilesDropdown.innerHTML = recentFilesStateMarkup('recentFilesLoading', 'Loading...');
    try {
        const allItems = await fetchRecentFileItems();
        const items = (isAgentMode ? allItems : allItems.filter(item => item.kind === 'image')).slice(0, 16);
        if (!items.length) {
            recentFilesDropdown.innerHTML = recentFilesStateMarkup('recentFilesEmpty', 'No recent files');
            return;
        }
        recentFilesDropdown.innerHTML = '';
        items.forEach(item => {
            const btn = document.createElement('button');
            btn.className = 'recent-file-item';
            btn.type = 'button';
            btn.setAttribute('role', 'menuitem');
            const icon = getAttachmentIcon(item.name, item.contentType || '');
            const previewMarkup = item.kind === 'image'
                ? `<img class="recent-file-thumb" src="${escapeAttr(item.url)}" alt="">`
                : `<span class="recent-file-icon" style="color:${escapeAttr(icon.iconColor)}"><i class="fa-solid ${escapeAttr(icon.iconClass)}"></i></span>`;
            btn.innerHTML = `
                ${previewMarkup}
                <span class="recent-file-name">${escapeAttr(item.name)}</span>
            `;
            btn.addEventListener('click', async (e) => {
                e.stopPropagation();
                try {
                    const res = await fetch(item.url, { cache: 'no-store' });
                    if (!res.ok) throw new Error(`RECENT_FILE_HTTP_${res.status}`);
                    const blob = await res.blob();
                    const filename = item.sourceName || item.name || (item.kind === 'pdf' ? 'document.pdf' : 'image.png');
                    const fileType = getMimeTypeForFilename(filename, blob.type || item.contentType || '');
                    const file = new File([blob], filename, { type: fileType });
                    pendingFiles.push(file);
                    renderAttachmentsPreview();
                    setUploadMenuOpen(false);
                } catch (err) {
                    console.error('Failed to add recent file', err);
                    showToast(tUi('recentFilesLoadError', 'Unable to load recent files'), true);
                }
            });
            recentFilesDropdown.appendChild(btn);
        });
    } catch (err) {
        console.error('Failed to render recent files', err);
        recentFilesDropdown.innerHTML = recentFilesStateMarkup('recentFilesLoadError', 'Unable to load recent files');
    }
}

if (imgUploadBtn && imageInput) {
    imgUploadBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        if (!currentUserId) {
            showGuestLoginPrompt(false);
            return;
        }
        setModeDropdownOpen(false);
        if (connectorsContainer) connectorsContainer.classList.remove('open');
        if (isMobileModePickerLayout()) {
            if (isMobileUploadSheetOpen()) closeMobileUploadSheet({ restoreFocus: true });
            else openMobileUploadSheet();
            return;
        }
        const nextOpen = !uploadMenuShell?.classList.contains('open');
        setUploadMenuOpen(nextOpen);
        if (nextOpen) fetchRecentFileItems().catch(err => console.warn('Recent file preload failed', err));
    });

    if (uploadFileMenuItem) {
        uploadFileMenuItem.addEventListener('click', (e) => {
            e.stopPropagation();
            openUploadPickerFromMenu();
        });
    }

    if (recentFilesMenuItem) {
        recentFilesMenuItem.addEventListener('mouseenter', () => {
            if (uploadMenuShell?.classList.contains('open')) setRecentFilesOpen(true);
        });
        recentFilesMenuItem.addEventListener('click', (e) => {
            e.stopPropagation();
            setRecentFilesOpen(!uploadMenuShell?.classList.contains('recent-open'));
        });
    }

    if (skillsMenuItem) {
        skillsMenuItem.addEventListener('mouseenter', () => {
            if (uploadMenuShell?.classList.contains('open')) setSkillsMenuOpen(true);
        });
        skillsMenuItem.addEventListener('click', (e) => {
            e.stopPropagation();
            setSkillsMenuOpen(!uploadMenuShell?.classList.contains('skills-open'));
        });
    }

    if (uploadFileMenuItem) {
        uploadFileMenuItem.addEventListener('mouseenter', () => {
            setRecentFilesOpen(false);
            setSkillsMenuOpen(false);
        });
    }

    if (recentFilesDropdown) {
        recentFilesDropdown.addEventListener('click', (e) => e.stopPropagation());
    }

    if (skillsDropdown) {
        skillsDropdown.addEventListener('click', (e) => {
            e.stopPropagation();
            const item = e.target.closest('.skill-file-item[data-skill]');
            if (!item) return;
            const skill = item.dataset.skill || '';
            setSelectedSkillType(skill);
            setUploadMenuOpen(false);
            userInput?.focus();
            const label = skill.toUpperCase();
            showToast(tUi('skillSelectedToast', '{type} skill selected').replace('{type}', label));
        });
    }

    document.addEventListener('click', (e) => {
        if (mobileUploadSheet?.contains(e.target)) return;
        if (uploadMenuShell && !uploadMenuShell.contains(e.target)) setUploadMenuOpen(false);
    });

    document.addEventListener('keydown', (e) => {
        if (e.key !== 'Escape') return;
        if (isMobileUploadSheetOpen()) {
            e.preventDefault();
            closeMobileUploadSheet({ restoreFocus: true });
            return;
        }
        setUploadMenuOpen(false);
    });

    imageInput.addEventListener('change', (e) => {
        const newFiles = Array.from(e.target.files);
        newFiles.forEach(f => {
            if (f.size > 5 * 1024 * 1024) {
                alert(`Image ${f.name} is too large. Max 5MB per image.`);
                return;
            }
            pendingFiles.push(f);
        });
        renderAttachmentsPreview();
        imageInput.value = '';
    });
}

if (mobileUploadSheet) {
    document.getElementById('mobileUploadPhotoBtn')?.addEventListener('click', () => {
        // Keep this native picker call inside the original tap's activation
        // window. Mobile Safari otherwise may require a second tap after a
        // dialog closes before it permits the file chooser to open.
        imageInput?.click();
        closeMobileUploadSheet();
    });
    document.getElementById('mobileUploadFileBtn')?.addEventListener('click', () => {
        fileInput?.click();
        closeMobileUploadSheet();
    });
    document.getElementById('mobileUploadRecentBtn')?.addEventListener('click', () => {
        setMobileUploadView('recent');
        renderMobileRecentFiles();
    });
    document.getElementById('mobileUploadSkillsBtn')?.addEventListener('click', () => {
        setMobileUploadView('skills');
    });
    document.getElementById('mobileUploadRecentBackBtn')?.addEventListener('click', () => setMobileUploadView('actions'));
    document.getElementById('mobileUploadSkillsBackBtn')?.addEventListener('click', () => setMobileUploadView('actions'));
    mobileUploadSheet.querySelectorAll('[data-mobile-skill]').forEach(button => {
        button.addEventListener('click', () => {
            setSelectedSkillType(button.dataset.mobileSkill || '');
            closeMobileUploadSheet();
            userInput?.focus();
        });
    });
    mobileUploadSheetCloseBtn?.addEventListener('click', () => closeMobileUploadSheet({ restoreFocus: true }));
    mobileUploadSheet.addEventListener('click', event => {
        if (event.target === mobileUploadSheet) closeMobileUploadSheet({ restoreFocus: true });
    });
    mobileUploadSheet.addEventListener('cancel', () => {
        window.setTimeout(() => (mobileUploadSheetReturnFocus || imgUploadBtn)?.focus?.({ preventScroll: true }), 0);
    });
    mobileUploadSheet.addEventListener('close', () => {
        document.body.classList.remove('mobile-upload-sheet-open');
        imgUploadBtn?.setAttribute('aria-expanded', 'false');
        setMobileUploadView('actions');
    });
}

window.addEventListener('resize', () => {
    if (!isMobileModePickerLayout()) closeMobileUploadSheet();
});

fileInput.addEventListener('change', (e) => {
    const newFiles = Array.from(e.target.files);
    newFiles.forEach(f => {
        if (f.size > 20 * 1024 * 1024) {
            alert(`File ${f.name} is too large. Max 20MB.`);
            return;
        }
        pendingFiles.push(f);
    });
    renderAttachmentsPreview();
    // reset input
    fileInput.value = '';
});

function removePendingFile(index) {
    pendingFiles.splice(index, 1);
    renderAttachmentsPreview();
}

function renderAttachmentsPreview() {
    pendingPreviewObjectUrls.forEach(url => URL.revokeObjectURL(url));
    pendingPreviewObjectUrls = [];
    attachmentsPreview.innerHTML = '';
    pendingFiles.forEach((f, idx) => {
        const isImg = isImageFile(f);
        const pill = document.createElement('div');
        pill.className = `file-pill ${isImg ? 'image-preview-pill' : ''}`;
        const { iconClass } = getAttachmentIcon(f.name, f.type || '');
        const sizeStr = formatFileSize(f.size);

        if (isImg) {
            const previewUrl = URL.createObjectURL(f);
            pendingPreviewObjectUrls.push(previewUrl);
            pill.innerHTML = `
                <img class="file-pill-thumb" src="${previewUrl}" alt="${escapeAttr(f.name)}">
                <span class="file-pill-tooltip">${escapeAttr(f.name || 'image')}</span>
                <button class="file-pill-remove image-pill-remove" onclick="removePendingFile(${idx})" aria-label="Remove image">
                    <i class="fa-solid fa-xmark"></i>
                </button>
            `;
        } else {
            pill.innerHTML = `
                <i class="fa-solid ${iconClass}"></i>
                <span class="file-pill-name">${escapeAttr(f.name)}</span>
                <span class="file-pill-size">${sizeStr}</span>
                <button class="file-pill-remove" onclick="removePendingFile(${idx})" aria-label="Remove file">
                    <i class="fa-solid fa-xmark"></i>
                </button>
            `;
        }
        attachmentsPreview.appendChild(pill);
    });
    window.requestAnimationFrame(syncComposerHeightVar);
}

function renderMessageAttachments(attachmentsArr) {
    // Legacy function kept for compatibility — now returns empty
    return '';
}

function createAttachmentCard(att) {
    const card = document.createElement('div');
    card.className = 'user-msg-attachment-card';
    const attName = att.original_name || 'file';
    const icon = getAttachmentIcon(attName, att.content_type || '');
    let iconClass = icon.iconClass;
    let iconColor = icon.iconColor;
    const isPdf = att.content_type === 'application/pdf' || (att.original_name && att.original_name.toLowerCase().endsWith('.pdf'));
    const isImage = isImageAttachment(att);
    const sizeStr = att.size ? formatFileSize(att.size) : '';

    // Construct the asset URL: prefer att.url, fallback to building from saved_path or file_id
    let assetUrl = att.url || null;
    if (!assetUrl && att.saved_path) {
        // Extract filename from saved_path (e.g. "uuid.pdf" from GridFS key)
        const parts = att.saved_path.replace(/\\/g, '/').split('/');
        const fname = parts[parts.length - 1];
        assetUrl = '/uploads/' + fname;
    }
    if (!assetUrl && att.file_id) {
        const ext = att.original_name ? att.original_name.substring(att.original_name.lastIndexOf('.')) : '.pdf';
        assetUrl = '/uploads/' + att.file_id + ext;
    }

    const safeAssetUrl = normalizeAssetUrl(assetUrl);
    if (isImage && safeAssetUrl) {
        card.classList.add('image-attachment-card');
        card.innerHTML = `
            <img class="att-thumb" src="${escapeAttr(safeAssetUrl)}" alt="${escapeAttr(attName)}" loading="lazy">
            <span class="att-name">${escapeAttr(att.original_name || 'image')}</span>
            ${sizeStr ? `<span class="att-size">(${sizeStr})</span>` : ''}
        `;
    } else {
        card.innerHTML = `
            <div class="att-icon" style="color:${iconColor}">
                <i class="fa-solid ${iconClass}"></i>
            </div>
            <span class="att-name">${escapeAttr(att.original_name || 'file')}</span>
            ${sizeStr ? `<span class="att-size">(${sizeStr})</span>` : ''}
        `;
    }

    if (isPdf && safeAssetUrl) {
        card.style.cursor = 'pointer';
        card.addEventListener('click', () => openPdfPreview(safeAssetUrl, att.original_name));
    }

    return card;
}

// ============ PDF Preview Modal ============
function openPdfPreview(url, filename) {
    const overlay = document.getElementById('pdfPreviewOverlay');
    const frame = document.getElementById('pdfPreviewFrame');
    const nameEl = document.getElementById('pdfPreviewFilename');

    if (!overlay || !frame) return;

    nameEl.textContent = filename || 'Document.pdf';
    frame.src = url;
    overlay.classList.add('show');
    document.body.style.overflow = 'hidden';
    markMobileHistoryOverlayOpen('pdf');
}

function closePdfPreviewRaw() {
    const overlay = document.getElementById('pdfPreviewOverlay');
    const frame = document.getElementById('pdfPreviewFrame');

    if (overlay) overlay.classList.remove('show');
    if (frame) frame.src = '';
    document.body.style.overflow = '';
}

function closePdfPreview() {
    const overlay = document.getElementById('pdfPreviewOverlay');
    if (!overlay?.classList.contains('show')) return;
    closePdfPreviewRaw();
    markMobileHistoryOverlayClosed('pdf');
}

registerMobileHistoryOverlay('pdf', closePdfPreviewRaw);

// PDF Preview event listeners
(function initPdfPreview() {
    const overlay = document.getElementById('pdfPreviewOverlay');
    const closeBtn = document.getElementById('pdfPreviewCloseBtn');

    if (closeBtn) {
        closeBtn.addEventListener('click', closePdfPreview);
    }
    if (overlay) {
        overlay.addEventListener('click', (e) => {
            if (e.target === overlay) closePdfPreview();
        });
    }

    // Escape key to close
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') closePdfPreview();
    });
})();

// ============ Event Listeners ============
submitBtn.addEventListener('click', () => handleSend(false, null));
userInput.addEventListener('input', resizeComposer);
window.addEventListener('resize', updateSelectedSkillUi);
userInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
        e.preventDefault();
        handleSend(false, null);
    }
});
userInput.addEventListener('paste', (e) => {
    const items = e.clipboardData && e.clipboardData.items;
    if (!items) return;
    for (const item of items) {
        if (!item.type.startsWith('image/')) continue;
        const file = item.getAsFile();
        if (!file) continue;
        if (!currentUserId) { showGuestLoginPrompt(false); return; }
        if (file.size > 5 * 1024 * 1024) {
            alert(`Pasted image is too large. Max 5MB.`);
            return;
        }
        const ext = file.type.split('/')[1] || 'png';
        const named = new File([file], `pasted-image-${Date.now()}.${ext}`, { type: file.type });
        pendingFiles.push(named);
    }
    renderAttachmentsPreview();
});
resizeComposer();

// ============ Auth ============
// Auth logic moved to index.html and modern scripts.

// ============ Scroll Listener for Source Cards ============
document.addEventListener('wheel', (e) => {
    const scrollContainer = e.target.closest('.sources-scroll');
    if (scrollContainer && Math.abs(e.deltaY) > 0) {
        const atLeft = scrollContainer.scrollLeft <= 0;
        const atRight = Math.ceil(scrollContainer.scrollLeft) >= (scrollContainer.scrollWidth - scrollContainer.clientWidth);

        if ((e.deltaY < 0 && atLeft) || (e.deltaY > 0 && atRight)) return;

        e.preventDefault();
        scrollContainer.scrollLeft += e.deltaY;
    }
}, { passive: false });

// ==========================================
// History Search Modal Logic
// ==========================================

const searchHistoryModal = document.getElementById('searchHistoryModal');
const openSearchModalBtn = document.getElementById('openSearchModalBtn');
const closeSearchModalBtn = document.getElementById('closeSearchModalBtn');
const modalHistoryList = document.getElementById('modalHistoryList');
const historySearchInput = document.getElementById('historySearchInput');
const previewEmptyState = document.getElementById('previewEmptyState');
const previewContent = document.getElementById('previewContent');
const previewTitle = document.getElementById('previewTitle');
const previewMessages = document.getElementById('previewMessages');

let previewHoverTimer = null;
let currentPreviewChatId = null;

function closeSearchHistoryModalRaw() {
    searchHistoryModal?.classList.remove('show');
    clearTimeout(previewHoverTimer);
}

function closeSearchHistoryModal() {
    if (!searchHistoryModal?.classList.contains('show')) return;
    closeSearchHistoryModalRaw();
    markMobileHistoryOverlayClosed('search');
}

registerMobileHistoryOverlay('search', closeSearchHistoryModalRaw);

function resetSearchPreview(message = 'Select a conversation to preview') {
    clearTimeout(previewHoverTimer);
    currentPreviewChatId = null;
    previewEmptyState.textContent = message;
    previewEmptyState.classList.remove('hidden');
    previewContent.classList.add('hidden');
    previewTitle.textContent = '';
    previewMessages.innerHTML = '';
    document.querySelectorAll('.history-search-list .history-item').forEach(el => el.classList.remove('active-preview'));
}

function setSearchPlaceholder(message) {
    modalHistoryList.innerHTML = `<li class="search-empty-state">${message}</li>`;
}

function getHistoryGroupName(date) {
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    const yesterday = new Date(today);
    yesterday.setDate(yesterday.getDate() - 1);
    const chatDate = new Date(date);
    chatDate.setHours(0, 0, 0, 0);

    if (chatDate.getTime() === today.getTime()) return 'Today';
    if (chatDate.getTime() === yesterday.getTime()) return 'Yesterday';

    const diffDays = Math.ceil(Math.abs(today - chatDate) / (1000 * 60 * 60 * 24));
    if (diffDays <= 7) return 'Previous 7 Days';
    if (diffDays <= 30) return 'Previous 30 Days';
    return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}`;
}

// Open Search Modal
openSearchModalBtn.addEventListener('click', async () => {
    currentUserId = localStorage.getItem('pepperUserId') || null;
    if (isMobileLayout()) setSidebarCollapsed(true, { skipHistory: true });
    searchHistoryModal.classList.add('show');
    markMobileHistoryOverlayOpen('search');
    historySearchInput.value = '';
    resetSearchPreview();

    if (!currentUserId) {
        historySearchInput.disabled = true;
        historySearchInput.placeholder = 'Login to search your conversations';
        setSearchPlaceholder('Please log in to view conversation history.');
        resetSearchPreview('Your history is private. Log in to search it.');
        return;
    }

    historySearchInput.disabled = false;
    historySearchInput.placeholder = 'Search conversations';
    setSearchPlaceholder('Loading conversations...');

    // Fetch History
    try {
        const res = await fetch('/api/history', { headers: authenticatedHeaders() });
        const data = await res.json();
        if (res.ok && data.chats) {
            const groups = {};

            data.chats.forEach(chat => {
                const date = chat.updated_at ? new Date(chat.updated_at) : new Date();
                const groupName = getHistoryGroupName(date);
                if (!groups[groupName]) groups[groupName] = [];
                groups[groupName].push({
                    id: chat._id,
                    title: chat.title || 'New Chat',
                    updatedAt: date,
                    agentMode: !!chat.agent_mode
                });
            });
            renderSearchModalHistory(groups);
        } else {
            setSearchPlaceholder('Failed to load history.');
        }
    } catch (e) {
        setSearchPlaceholder('Failed connecting to server.');
    }

    setTimeout(() => historySearchInput.focus(), 100);
});

// Close Mode
closeSearchModalBtn.addEventListener('click', () => {
    closeSearchHistoryModal();
});

// Close modal when clicking outside
searchHistoryModal.addEventListener('click', (e) => {
    if (e.target === searchHistoryModal) {
        closeSearchHistoryModal();
    }
});

// Real-time Text Filter
historySearchInput.addEventListener('input', (e) => {
    const q = e.target.value.toLowerCase().trim();
    const items = modalHistoryList.querySelectorAll('.history-item');
    items.forEach(item => {
        const text = item.querySelector('.history-item-text').innerText.toLowerCase();
        item.style.display = text.includes(q) ? 'flex' : 'none';
    });

    modalHistoryList.querySelectorAll('.history-group-title').forEach(group => {
        let sibling = group.nextElementSibling;
        let hasVisibleItem = false;
        while (sibling && !sibling.classList.contains('history-group-title')) {
            if (sibling.classList.contains('history-item') && sibling.style.display !== 'none') {
                hasVisibleItem = true;
                break;
            }
            sibling = sibling.nextElementSibling;
        }
        group.style.display = hasVisibleItem ? 'block' : 'none';
    });
});

function renderSearchModalHistory(groups) {
    modalHistoryList.innerHTML = '';
    if (Object.keys(groups).length === 0) {
        setSearchPlaceholder('No conversation history yet.');
        return;
    }

    for (const group in groups) {
        if (groups[group].length === 0) continue;
        const groupHeader = document.createElement('div');
        groupHeader.className = 'history-group-title';
        groupHeader.innerText = group;
        modalHistoryList.appendChild(groupHeader);

        groups[group].forEach(entry => {
            const li = document.createElement('li');
            li.className = 'history-item';
            li.dataset.chatId = entry.id;
            if (entry.id === currentChatId) li.classList.add('active');

            const timeLabel = entry.updatedAt.toLocaleString([], {
                month: 'short',
                day: 'numeric',
                hour: '2-digit',
                minute: '2-digit'
            });
            const openLabel = tUi('historyOpenAction', 'Open conversation');
            const renameLabel = tUi('historyRenameAction', 'Rename conversation');
            const deleteLabel = tUi('historyDeleteAction', 'Delete conversation');

            li.innerHTML = `
                <div class="history-item-main">
                    <div class="history-item-text"></div>
                    <div class="history-item-meta">${entry.agentMode ? 'Agent' : 'Chat'} · ${timeLabel}</div>
                </div>
                <div class="history-item-actions">
                    <button class="modal-action-btn open-btn" type="button" title="${escapeAttr(openLabel)}" aria-label="${escapeAttr(openLabel)}" data-i18n-title="historyOpenAction" data-i18n-aria-label="historyOpenAction"><i class="fa-solid fa-arrow-up-right-from-square" aria-hidden="true"></i></button>
                    <button class="modal-action-btn edit-btn" type="button" title="${escapeAttr(renameLabel)}" aria-label="${escapeAttr(renameLabel)}" data-i18n-title="historyRenameAction" data-i18n-aria-label="historyRenameAction"><i class="fa-solid fa-pencil" aria-hidden="true"></i></button>
                    <button class="modal-action-btn delete-btn" type="button" title="${escapeAttr(deleteLabel)}" aria-label="${escapeAttr(deleteLabel)}" data-i18n-title="historyDeleteAction" data-i18n-aria-label="historyDeleteAction"><i class="fa-regular fa-trash-can" aria-hidden="true"></i></button>
                </div>
            `;

            const openBtn = li.querySelector('.open-btn');
            const editBtn = li.querySelector('.edit-btn');
            const deleteBtn = li.querySelector('.delete-btn');
            const textDiv = li.querySelector('.history-item-text');
            textDiv.innerText = entry.title;
            textDiv.title = entry.title;
            const openEntry = () => openHistoryChat(entry.id, { closeModal: true });

            // Hover logic with 250ms debounce
            li.addEventListener('mouseenter', () => {
                clearTimeout(previewHoverTimer);
                previewHoverTimer = setTimeout(() => {
                    loadChatPreview(entry.id, entry.title, li);
                }, 250);
            });

            li.addEventListener('mouseleave', () => {
                clearTimeout(previewHoverTimer);
            });

            li.addEventListener('click', (e) => {
                if (e.target.closest('.history-item-actions')) return;
                openEntry();
            });

            // Actions
            openBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                openEntry();
            });

            editBtn.addEventListener('click', async (e) => {
                e.stopPropagation();
                const newTitle = prompt("Rename chat:", entry.title);
                if (newTitle && newTitle.trim() && newTitle.trim() !== entry.title) {
                    try {
                        const rr = await fetch(historyChatUrl(entry.id), {
                            method: 'PUT',
                            headers: authenticatedHeaders({ 'Content-Type': 'application/json' }),
                            body: JSON.stringify({ title: newTitle.trim() })
                        });
                        if (rr.ok) {
                            entry.title = newTitle.trim();
                            textDiv.innerText = newTitle.trim();
                            textDiv.title = newTitle.trim();
                            loadHistory(); // refresh global sidebar
                        }
                    } catch(err) { console.error("Rename failed", err); }
                }
            });

            deleteBtn.addEventListener('click', async (e) => {
                e.stopPropagation();
                deleteHistoryChat(entry.id, li, {
                    clearPreview: true
                });
            });
            modalHistoryList.appendChild(li);
        });
    }
}

async function loadChatPreview(chatId, title, liElement) {
    if (!currentUserId) return;
    if (currentPreviewChatId === chatId) return; // already viewing

    // highlight actively previewed item
    document.querySelectorAll('.history-search-list .history-item').forEach(el => el.classList.remove('active-preview'));
    liElement.classList.add('active-preview');

    currentPreviewChatId = chatId;
    previewEmptyState.classList.add('hidden');
    previewContent.classList.remove('hidden');
    previewTitle.innerText = title;
    previewMessages.innerHTML = `<div class="search-status">Fetching logs...</div>`;

    try {
        const res = await fetch(`/api/history/${chatId}`, { headers: authenticatedHeaders() });
        const data = await res.json();
        if (res.ok && data.chat) {
            const msgs = data.chat.messages || [];
            previewMessages.innerHTML = '';

            if (msgs.length === 0) {
                previewMessages.innerHTML = `<div class="search-status">This chat is empty.</div>`;
                return;
            }

            msgs.forEach(m => {
                const wrapper = document.createElement('div');
                wrapper.className = `preview-msg ${m.role}`;

                const roleDiv = document.createElement('div');
                roleDiv.className = 'preview-msg-role';
                roleDiv.innerText = m.role === 'assistant' ? 'bisnes.ai' : 'You';

                const contentDiv = document.createElement('div');
                contentDiv.className = 'preview-msg-body';

                if (m.role === 'assistant') {
                    // Strip huge <think> blocks for preview clarity
                    let content = m.content;
                    content = content.replace(/<think>[\s\S]*?<\/think>/g, '<div class="preview-think-note">Thought process completed</div>');
                    contentDiv.innerHTML = renderMd(content);
                } else {
                    contentDiv.innerText = m.content;
                }

                wrapper.appendChild(roleDiv);
                wrapper.appendChild(contentDiv);
                previewMessages.appendChild(wrapper);
            });
        } else {
            previewMessages.innerHTML = `<div class="search-status">Failed to parse chat logs.</div>`;
        }
    } catch(e) {
        previewMessages.innerHTML = `<div class="search-status">Failed to fetch preview.</div>`;
    }
}

// ============ Guest Navigation: Settings Dropdown & Language ============
(function initGuestNav() {
    const nav = document.getElementById('guestNav');
    const gearBtn = document.getElementById('settingsGearBtn');
    const dropdown = document.getElementById('settingsDropdown');
    if (!nav || !gearBtn || !dropdown) return;

    // Hide nav if user is logged in
    function updateNavVisibility() {
        nav.style.display = currentUserId ? 'none' : 'flex';
        syncGuestAccessState();
    }
    updateNavVisibility();

    // Observe login state changes
    const origSetItem = localStorage.setItem.bind(localStorage);
    const origRemoveItem = localStorage.removeItem.bind(localStorage);
    localStorage.setItem = function(key, val) {
        origSetItem(key, val);
        if (key === 'pepperUserId' || key === 'pepperUsername') setTimeout(updateNavVisibility, 100);
    };
    localStorage.removeItem = function(key) {
        origRemoveItem(key);
        if (key === 'pepperUserId' || key === 'pepperUsername') setTimeout(updateNavVisibility, 100);
    };

    // ── Gear Button: Toggle Dropdown ──
    gearBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        const isOpen = dropdown.classList.contains('open');
        dropdown.classList.toggle('open', !isOpen);
        gearBtn.classList.toggle('active', !isOpen);
    });

    // Close dropdown on outside click
    document.addEventListener('click', (e) => {
        if (!dropdown.contains(e.target) && e.target !== gearBtn) {
            dropdown.classList.remove('open');
            gearBtn.classList.remove('active');
        }
    });

    // ── Login / Register: now plain <a href>, no JS needed ──
    const guestLoginBtn = document.getElementById('guestLoginBtn');
    const guestRegisterBtn = document.getElementById('guestRegisterBtn');

    // ── Language Selector ──
    const savedLang = getPreferredLanguage();
    applyLang(savedLang);

    nav.querySelectorAll('.lang-btn').forEach(btn => {
        if (btn.dataset.lang === savedLang) btn.classList.add('active');
        else btn.classList.remove('active');

        btn.addEventListener('click', () => {
            applyLang(btn.dataset.lang);
            localStorage.setItem('pepperLang', btn.dataset.lang);
            syncPreferenceControls();
            saveUserPreferences({ language: btn.dataset.lang });
        });
    });

    function applyLang(lang) {
        // Walk DOM and apply current i18next translations
        const updateDOM = () => {
            const tr = (key) => {
                const v = i18next.t(key);
                return (v && v !== key) ? v : null;
            };
            document.querySelectorAll('[data-i18n]').forEach(el => {
                const v = tr(el.dataset.i18n);
                if (v != null) el.textContent = v;
            });
            document.querySelectorAll('[data-i18n-placeholder]').forEach(el => {
                const v = tr(el.dataset.i18nPlaceholder);
                if (v != null) el.placeholder = v;
            });
            document.querySelectorAll('[data-i18n-title]').forEach(el => {
                const v = tr(el.dataset.i18nTitle);
                if (v != null) el.title = v;
            });
            document.querySelectorAll('[data-i18n-aria-label]').forEach(el => {
                const v = tr(el.dataset.i18nAriaLabel);
                if (v != null) el.setAttribute('aria-label', v);
            });
            if (!currentUserId) {
                const ud = document.getElementById('userDisplay');
                const v = tr('login');
                if (ud && v) ud.innerText = v;
            }
            window._pepperLang = i18next.getResourceBundle(i18next.language, 'translation') || {};
            window.applyPepperLang = applyLang;
            document.documentElement.setAttribute('lang', i18next.language);
            // The compact trigger is text-only (rather than a data-i18n node),
            // so explicitly refresh it after the async language bundle lands.
            syncSearchModeUi();
            if (logoContainer && isFirstMessage && !isAgentMode) {
                logoContainer.innerHTML = getNormalLandingMarkup();
            }
            updateGuestLimitBannerCopy();
            if (uploadMenuShell?.classList.contains('recent-open')) {
                renderRecentFilesMenu();
            }
            updateSelectedSkillUi();
        };

        if (!window._i18nReady) {
            // First call: initialize i18next + http-backend (loads /static/locales/{{lng}}.json)
            window._i18nReady = i18next
                .use(i18nextHttpBackend)
                .init({
                    lng: lang,
                    fallbackLng: 'en',
                    supportedLngs: SUPPORTED_LANGS,
                    backend: { loadPath: '/static/locales/{{lng}}.json' }
                })
                .then(updateDOM)
                .catch(err => console.warn('i18next init failed', err));
        } else {
            // Subsequent calls: just switch language
            window._i18nReady = i18next.changeLanguage(lang)
                .then(updateDOM)
                .catch(err => console.warn('i18next changeLanguage failed', err));
        }
    }
})();
loadUserPreferences();

// ============ Sidebar User Context Menu & Avatar Sync ============
(function initUserContextMenu() {
    const showLoginBtn = document.getElementById('showLoginBtn');
    const contextMenu = document.getElementById('userContextMenu');
    const avatarDisplay = document.getElementById('avatarDisplay');
    const userDisplayName = document.getElementById('userDisplayName');
    const userEmailDisplay = document.getElementById('userEmailDisplay');
    const logoutBtn = document.getElementById('ctxLogoutBtn');
    const accountBtn = document.getElementById('ctxAccountBtn');
    const settingsBtn = document.getElementById('ctxSettingsMenuBtn');
    const settingsBlock = document.getElementById('ctxSettingsBlock');
    const connectorsBtn = document.getElementById('ctxConnectorsBtn');
    const upgradeBtn = document.getElementById('ctxUpgradeBtn');

    function hashToHSL(str) {
        let hash = 0;
        for (let i = 0; i < str.length; i++) {
            hash = str.charCodeAt(i) + ((hash << 5) - hash);
        }
        const h = Math.abs(hash % 360);
        return `hsl(${h}, 70%, 60%)`;
    }

    function hydrateUser() {
        const jwt = localStorage.getItem('pepperSession');
        const username = localStorage.getItem('pepperUsername');
        const displayName = localStorage.getItem('pepperDisplayName');
        const avatarUrl = localStorage.getItem('pepperAvatar');

        // If no valid JWT, clear stale user info and show default state
        if (!jwt) {
            if (userDisplayName) userDisplayName.innerText = 'Login';
            if (userEmailDisplay) userEmailDisplay.innerText = 'Register';
            if (avatarDisplay) {
                avatarDisplay.innerHTML = '';
                avatarDisplay.style.background = '#444';
            }
            // Clean up stale localStorage entries
            localStorage.removeItem('pepperUsername');
            localStorage.removeItem('pepperDisplayName');
            localStorage.removeItem('pepperAvatar');
            localStorage.removeItem('pepperUserId');
            return;
        }

        const profileName = displayName || (username && username.includes('@') ? username.split('@')[0] : username);
        if (username && username.includes('@')) {
            if (userDisplayName) userDisplayName.innerText = profileName;
            if (userEmailDisplay) userEmailDisplay.innerText = username;

            if (avatarUrl && avatarDisplay) {
                avatarDisplay.innerHTML = `<img src="${escapeAttr(resolveAvatarSrc(avatarUrl))}" alt="Avatar" referrerpolicy="no-referrer">`;
                avatarDisplay.style.background = 'transparent';
            } else if (avatarDisplay) {
                const firstLetter = (profileName || username).charAt(0).toUpperCase();
                const bgColor = hashToHSL(profileName || username);
                avatarDisplay.textContent = firstLetter;
                avatarDisplay.style.background = `linear-gradient(135deg, ${bgColor}, #333333)`;
            }
        } else if (profileName) {
            if (userDisplayName) userDisplayName.innerText = profileName;
            if (userEmailDisplay) userEmailDisplay.innerText = '';

            if (avatarDisplay) {
                const firstLetter = profileName.charAt(0).toUpperCase();
                const bgColor = hashToHSL(profileName);
                avatarDisplay.textContent = firstLetter;
                avatarDisplay.style.background = `linear-gradient(135deg, ${bgColor}, #333333)`;
            }
        }
    }
    hydrateUser();

    function closeContextMenu() {
        if (contextMenu) contextMenu.classList.remove('show');
        if (showLoginBtn) showLoginBtn.setAttribute('aria-expanded', 'false');
        if (settingsBlock) settingsBlock.classList.remove('show');
        if (settingsBtn) {
            settingsBtn.classList.remove('is-open');
            settingsBtn.setAttribute('aria-expanded', 'false');
        }
    }

    if(showLoginBtn && contextMenu) {
        showLoginBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            if (!localStorage.getItem('pepperUserId')) {
                window.location.href = '/static/login.html';
            } else {
                const isShowing = contextMenu.classList.contains('show');
                contextMenu.classList.toggle('show', !isShowing);
                showLoginBtn.setAttribute('aria-expanded', String(!isShowing));
                if (isShowing) {
                    if (settingsBlock) settingsBlock.classList.remove('show');
                    if (settingsBtn) {
                        settingsBtn.classList.remove('is-open');
                        settingsBtn.setAttribute('aria-expanded', 'false');
                    }
                }
            }
        });

        document.addEventListener('click', (e) => {
            if (!contextMenu.contains(e.target) && !showLoginBtn.contains(e.target)) {
                closeContextMenu();
            }
        });
    }

    const closeAuthBtn = document.getElementById('closeAuthModalBtn');
    const authModal = document.getElementById('authRequiredModal');
    if (closeAuthBtn && authModal) {
        closeAuthBtn.addEventListener('click', () => {
            authModal.classList.remove('show');
        });
    }

    if(logoutBtn) {
        logoutBtn.addEventListener('click', async () => {
            try {
                await fetch('/api/auth/logout', { method: 'POST', headers: authenticatedHeaders() });
            } catch (error) {
                console.warn('Could not clear the server session', error);
            }
            clearStoredAccountFields();
            window.location.href = '/static/login.html';
        });
    }

    if (accountBtn) {
        accountBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            closeContextMenu();
            window.dispatchEvent(new CustomEvent('open-mof-account-page'));
        });
    }

    if(settingsBtn && settingsBlock) {
        settingsBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            const nextOpen = !settingsBlock.classList.contains('show');
            settingsBlock.classList.toggle('show', nextOpen);
            settingsBtn.classList.toggle('is-open', nextOpen);
            settingsBtn.setAttribute('aria-expanded', nextOpen ? 'true' : 'false');
        });
    }

    if (connectorsBtn) {
        connectorsBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            closeContextMenu();
            if (!isAgentMode) openFreshAgentChat(false);
            showToast(tUi('ctxConnectorsHint', 'Agent mode is open. Use connectors from the input box.'));
        });
    }

    if (upgradeBtn) {
        upgradeBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            closeContextMenu();
            showToast(tUi('ctxComingSoon', 'Coming soon'));
        });
    }

    const ctxLangSel = document.getElementById('ctxLangSelector');
    if (ctxLangSel) {
        ctxLangSel.querySelectorAll('.lang-btn').forEach(btn => {
            const currentLang = getPreferredLanguage();
            btn.classList.toggle('active', btn.dataset.lang === currentLang);
            btn.addEventListener('click', () => {
                localStorage.setItem('pepperLang', btn.dataset.lang);
                if (window.applyPepperLang) window.applyPepperLang(btn.dataset.lang);
                syncPreferenceControls();
                saveUserPreferences({ language: btn.dataset.lang });
            });
        });
    }
})();

// ============ Fullscreen Account Management ============
(function initAccountPage() {
    const overlay = document.getElementById('accountPageOverlay');
    const backBtn = document.getElementById('accountBackMainBtn');
    const welcomeName = document.getElementById('accountWelcomeName');
    const topAvatar = document.getElementById('accountTopAvatar');
    const langBtn = document.getElementById('accountLangSwitch');
    const langLabel = document.getElementById('accountLangLabel');
    const downloadBtn = document.getElementById('accountDownloadBtn');
    const deleteBtn = document.getElementById('accountDeleteBtn');
    const downloadDialog = document.getElementById('accountDownloadDialog');
    const downloadDialogClose = document.getElementById('accountDownloadDialogClose');
    const deleteDialog = document.getElementById('accountDeleteDialog');
    const deleteEmailPrompt = document.getElementById('accountDeleteEmailPrompt');
    const deleteEmailInput = document.getElementById('accountDeleteEmailInput');
    const deleteCancelBtn = document.getElementById('accountDeleteCancelBtn');
    const deleteConfirmBtn = document.getElementById('accountDeleteConfirmBtn');
    if (!overlay) return;

    const languageLabels = { en: 'EN', ms: 'BM' };
    const languageOrder = SUPPORTED_LANGS;
    const accountBusinessCategories = [
        'Food & Beverage', 'Retail & Wholesale', 'Manufacturing', 'Agriculture',
        'Construction', 'Professional Services', 'Education & Training',
        'Healthcare & Wellness', 'Beauty & Personal Care', 'Technology & Digital',
        'Creative & Media', 'Logistics & Transportation', 'Tourism & Hospitality',
        'Automotive', 'Others'
    ];
    const accountBusinessNatures = [
        'Product-Based', 'Service-Based', 'Trading', 'Manufacturing', 'Online Business',
        'Home-Based Business', 'Franchise', 'Social Enterprise', 'Cooperative', 'Others'
    ];
    const accountBusinessLabels = {
        zh: {
            'Food & Beverage': '餐饮', 'Retail & Wholesale': '零售与批发', 'Manufacturing': '制造业',
            'Agriculture': '农业', 'Construction': '建筑业', 'Professional Services': '专业服务',
            'Education & Training': '教育与培训', 'Healthcare & Wellness': '医疗与健康',
            'Beauty & Personal Care': '美容与个人护理', 'Technology & Digital': '科技与数码',
            'Creative & Media': '创意与媒体', 'Logistics & Transportation': '物流与运输',
            'Tourism & Hospitality': '旅游与酒店', 'Automotive': '汽车行业', 'Others': '其他',
            'Product-Based': '产品型', 'Service-Based': '服务型', 'Trading': '贸易',
            'Online Business': '线上业务', 'Home-Based Business': '居家业务', 'Franchise': '特许经营',
            'Social Enterprise': '社会企业', 'Cooperative': '合作社'
        },
        ms: {
            'Food & Beverage': 'Makanan & Minuman', 'Retail & Wholesale': 'Runcit & Borong',
            'Manufacturing': 'Pembuatan', 'Agriculture': 'Pertanian', 'Construction': 'Pembinaan',
            'Professional Services': 'Perkhidmatan Profesional', 'Education & Training': 'Pendidikan & Latihan',
            'Healthcare & Wellness': 'Kesihatan & Kesejahteraan', 'Beauty & Personal Care': 'Kecantikan & Penjagaan Diri',
            'Technology & Digital': 'Teknologi & Digital', 'Creative & Media': 'Kreatif & Media',
            'Logistics & Transportation': 'Logistik & Pengangkutan', 'Tourism & Hospitality': 'Pelancongan & Hospitaliti',
            'Automotive': 'Automotif', 'Others': 'Lain-lain', 'Product-Based': 'Berasaskan Produk',
            'Service-Based': 'Berasaskan Perkhidmatan', 'Trading': 'Perdagangan',
            'Online Business': 'Perniagaan Dalam Talian', 'Home-Based Business': 'Perniagaan Dari Rumah',
            'Franchise': 'Francais', 'Social Enterprise': 'Perusahaan Sosial', 'Cooperative': 'Koperasi'
        }
    };
    const accountCopy = {
        zh: {
            welcomePrefix: '欢迎，',
            sentenceEnd: '。',
            manageAccount: '管理您的 bisnes.ai 账户。',
            navAccount: '账户',
            navSecurity: '安全',
            navData: '数据',
            backHome: '返回主页面',
            profileTitle: '您的账户',
            profileSubtitle: '管理您的账户信息。',
            labelName: '全名',
            labelEmail: '邮箱',
            labelBusinessCategory: '业务类别（行业）',
            labelBusinessNature: '业务性质（业务类型）',
            editBusinessProfileBtn: '编辑资料',
            editBusinessProfileTitle: '编辑业务资料',
            editBusinessProfileDesc: '选择最符合您业务的类别和类型。',
            selectBusinessCategory: '选择业务类别',
            selectBusinessNature: '选择业务类型',
            businessProfileRequired: '请选择业务类别和业务类型。',
            businessProfileSaveFailed: '无法保存业务资料，请重试。',
            notProvided: '尚未填写',
            labelSubscription: '订阅',
            subscriptionText: '管理您的订阅',
            manageSubBtn: '管理 ↗',
            labelCreated: '账户创建',
            editNameBtn: '编辑姓名',
            updateEmailBtn: '更新邮箱',
            loginMethodsTitle: '登录方法',
            loginMethodsSubtitle: '管理您登录 bisnes.ai 的方式。',
            loginEmail: '邮箱和密码',
            loginEmailSub: '启用邮箱登录',
            loginAppleSub: '绑定您的手机号',
            btnEnabled: '启用',
            btnDisabled: '停用',
            btnConnect: '连接',
            securitySubtitle: '管理您的账户安全设置。',
            dataTitle: '您的数据',
            dataSubtitle: '管理您存储在 bisnes.ai 的个人数据。',
            cookieTitle: 'Cookie 设置',
            cookieDesc: '管理您的分析和广告 Cookie 偏好设置。',
            downloadTitle: '下载账户数据',
            downloadDesc: '您可以在下方下载与您的账户关联的所有数据。此数据包括存储在所有 bisnes.ai 产品中的一切。',
            deleteTitle: '删除账户',
            deleteDesc: '删除您的账户以及 bisnes.ai 平台上的关联数据。如果您在 30 天内再次登录，可以恢复您的账户。',
            manageBtn: '管理',
            downloadBtn: '下载',
            deleteBtn: '删除',
            downloadSentTitle: '邮件已发送!',
            downloadSentDesc: '我们将很快向您发送一封包含数据下载链接的邮件。',
            closeBtn: '关闭',
            deleteDialogTitle: '您确定吗?',
            deleteDialogDesc: '此操作将删除您所有与 bisnes.ai 关联的数据，并将您退出登录。如果您在 30 天内再次登录，可以恢复您的数据。30 天后您的数据将被永久删除。',
            deleteEmailPrefix: '输入您的邮箱 ',
            deleteEmailSuffix: ' 以确认',
            cancelBtn: '取消',
            saveBtn: '保存',
            editNameTitle: '编辑姓名',
            editNameLabel: '显示名称',
            updateEmailTitle: '更新邮箱',
            updateEmailLabel: '新邮箱地址',
            deleteMismatch: '请输入当前账号邮箱以确认删除。',
            cookiePrefTitle: '隐私偏好中心',
            cookiePrefDesc: '当您访问任何网站时，该网站可能会在您的浏览器中存储或检索信息，主要以 Cookie 的形式。这些信息通常不会直接识别您的身份，但可以为您提供更加个性化的网络体验。',
            cookieAllowAll: '全部允许',
            cookieManageTitle: '管理许可偏好',
            cookieEssential: '绝对必要的 Cookie',
            cookieAlwaysActive: '始终处于活动状态',
            cookieTargeting: '定向 Cookie',
            cookiePerformance: '性能 Cookie',
            cookieRejectAll: '全部拒绝',
            cookieConfirm: '确认我的选择',
            continueBtn: '继续',
            backBtn: '返回',
            verifyBtn: '验证',
            verifyEmailTitle: '验证新邮箱',
            verifyEmailDesc: '验证码已发送至',
            otpLabel: '验证码',
            emailAlreadyUsed: '您输入的邮箱地址已被使用。',
            securityPasswordTitle: '使用密码登录',
            securityPasswordDesc: '管理您账户的密码。',
            securitySetPasswordBtn: '设置密码',
            securityNewPasswordLabel: '新密码',
            securitySavePasswordBtn: '保存密码',
            securitySetPasswordSubtitle: '填写下方表单以更改您的登录密码。',
            passwordTooShort: '密码至少需要 8 个字符，首位为大写字母，并包含一个特殊符号。',
            googleUnlinkConfirm: '确定要解除 Google 账户绑定吗？',
            googleUnlinkTitle: '您可能会被退出登录',
            googleUnlinkDesc: '取消关联此方法可能会将您退出账户登录。',
            googleNotLinked: '请先在账户设置中绑定您的 Google 账号后再使用连接器。'
        },
        en: {
            welcomePrefix: 'Welcome, ',
            sentenceEnd: '.',
            manageAccount: 'Manage your bisnes.ai account.',
            navAccount: 'Account',
            navSecurity: 'Security',
            navData: 'Data',
            backHome: 'Back to home',
            profileTitle: 'Your account',
            profileSubtitle: 'Manage your account information.',
            labelName: 'Full name',
            labelEmail: 'Email',
            labelBusinessCategory: 'Business category (Industry)',
            labelBusinessNature: 'Business nature (Business type)',
            editBusinessProfileBtn: 'Edit details',
            editBusinessProfileTitle: 'Edit business details',
            editBusinessProfileDesc: 'Choose the category and type that best describe your business.',
            selectBusinessCategory: 'Select a business category',
            selectBusinessNature: 'Select a business type',
            businessProfileRequired: 'Select both your business category and business type.',
            businessProfileSaveFailed: 'Could not save your business details. Please try again.',
            notProvided: 'Not provided',
            labelSubscription: 'Subscription',
            subscriptionText: 'Manage your subscription',
            manageSubBtn: 'Manage ↗',
            labelCreated: 'Account created',
            editNameBtn: 'Edit name',
            updateEmailBtn: 'Update email',
            loginMethodsTitle: 'Login methods',
            loginMethodsSubtitle: 'Manage how you log in to bisnes.ai.',
            loginEmail: 'Email and password',
            loginEmailSub: 'Enable email login',
            loginAppleSub: 'Connect your phone number',
            btnEnabled: 'Enabled',
            btnDisabled: 'Disabled',
            btnConnect: 'Connect',
            securitySubtitle: 'Manage your account security settings.',
            dataTitle: 'Your data',
            dataSubtitle: 'Manage the personal data you store with bisnes.ai.',
            cookieTitle: 'Cookie settings',
            cookieDesc: 'Manage your analytics and advertising cookie preferences.',
            downloadTitle: 'Download account data',
            downloadDesc: 'You can download all data associated with your account below. This includes everything stored across bisnes.ai products.',
            deleteTitle: 'Delete account',
            deleteDesc: 'Delete your account and associated bisnes.ai platform data. If you log in again within 30 days, your account can be restored.',
            manageBtn: 'Manage',
            downloadBtn: 'Download',
            deleteBtn: 'Delete',
            downloadSentTitle: 'Email sent!',
            downloadSentDesc: 'We will soon send you an email containing a data download link.',
            closeBtn: 'Close',
            deleteDialogTitle: 'Are you sure?',
            deleteDialogDesc: 'This will delete all data associated with bisnes.ai and log you out. If you log in again within 30 days, your data can be restored. After 30 days your data will be permanently deleted.',
            deleteEmailPrefix: 'Enter your email ',
            deleteEmailSuffix: ' to confirm',
            cancelBtn: 'Cancel',
            saveBtn: 'Save',
            editNameTitle: 'Edit Name',
            editNameLabel: 'Display name',
            updateEmailTitle: 'Update Email',
            updateEmailLabel: 'New email address',
            deleteMismatch: 'Enter the current account email to confirm deletion.',
            cookiePrefTitle: 'Privacy Preference Centre',
            cookiePrefDesc: 'When you visit any website, it may store or retrieve information on your browser, mostly in the form of cookies. This information does not usually directly identify you, but it can give you a more personalised web experience.',
            cookieAllowAll: 'Allow All',
            cookieManageTitle: 'Manage Consent Preferences',
            cookieEssential: 'Strictly Necessary Cookies',
            cookieAlwaysActive: 'Always Active',
            cookieTargeting: 'Targeting Cookies',
            cookiePerformance: 'Performance Cookies',
            cookieRejectAll: 'Reject All',
            cookieConfirm: 'Confirm My Choices',
            continueBtn: 'Continue',
            backBtn: 'Back',
            verifyBtn: 'Verify',
            verifyEmailTitle: 'Verify new email',
            verifyEmailDesc: 'Verification code sent to',
            otpLabel: 'Verification code',
            emailAlreadyUsed: 'This email address is already in use.',
            securityPasswordTitle: 'Password login',
            securityPasswordDesc: 'Manage your account password.',
            securitySetPasswordBtn: 'Set password',
            securityNewPasswordLabel: 'New password',
            securitySavePasswordBtn: 'Save password',
            securitySetPasswordSubtitle: 'Fill in the form below to change your login password.',
            passwordTooShort: 'Password must have at least 8 characters, start with an uppercase letter, and include a special character.',
            googleUnlinkConfirm: 'Are you sure you want to unlink your Google account?',
            googleUnlinkTitle: 'You may be signed out',
            googleUnlinkDesc: 'Removing this method may sign you out of your account.',
            googleNotLinked: 'Please link your Google account in Account Settings before using connectors.'
        },
        ms: {
            welcomePrefix: 'Selamat datang, ',
            sentenceEnd: '.',
            manageAccount: 'Urus akaun bisnes.ai anda.',
            navAccount: 'Akaun',
            navSecurity: 'Keselamatan',
            navData: 'Data',
            backHome: 'Kembali ke halaman utama',
            profileTitle: 'Akaun anda',
            profileSubtitle: 'Urus maklumat akaun anda.',
            labelName: 'Nama penuh',
            labelEmail: 'E-mel',
            labelBusinessCategory: 'Kategori perniagaan (Industri)',
            labelBusinessNature: 'Jenis perniagaan',
            editBusinessProfileBtn: 'Edit maklumat',
            editBusinessProfileTitle: 'Edit maklumat perniagaan',
            editBusinessProfileDesc: 'Pilih kategori dan jenis yang paling sesuai dengan perniagaan anda.',
            selectBusinessCategory: 'Pilih kategori perniagaan',
            selectBusinessNature: 'Pilih jenis perniagaan',
            businessProfileRequired: 'Pilih kategori dan jenis perniagaan anda.',
            businessProfileSaveFailed: 'Maklumat perniagaan tidak dapat disimpan. Sila cuba lagi.',
            notProvided: 'Belum diberikan',
            labelSubscription: 'Langganan',
            subscriptionText: 'Urus langganan anda',
            manageSubBtn: 'Urus ↗',
            labelCreated: 'Akaun dibuat',
            editNameBtn: 'Edit nama',
            updateEmailBtn: 'Kemaskini e-mel',
            loginMethodsTitle: 'Kaedah log masuk',
            loginMethodsSubtitle: 'Urus cara anda log masuk ke bisnes.ai.',
            loginEmail: 'E-mel dan kata laluan',
            loginEmailSub: 'Aktifkan log masuk e-mel',
            loginAppleSub: 'Sambung nombor telefon anda',
            btnEnabled: 'Aktif',
            btnDisabled: 'Tidak aktif',
            btnConnect: 'Sambung',
            securitySubtitle: 'Urus tetapan keselamatan akaun anda.',
            dataTitle: 'Data anda',
            dataSubtitle: 'Urus data peribadi yang anda simpan di bisnes.ai.',
            cookieTitle: 'Tetapan Cookie',
            cookieDesc: 'Urus pilihan cookie analitik dan pengiklanan anda.',
            downloadTitle: 'Muat turun data akaun',
            downloadDesc: 'Anda boleh memuat turun semua data yang berkaitan dengan akaun anda. Data ini termasuk semua yang disimpan merentas produk bisnes.ai.',
            deleteTitle: 'Padam akaun',
            deleteDesc: 'Padam akaun anda dan data berkaitan pada platform bisnes.ai. Jika anda log masuk semula dalam 30 hari, akaun anda boleh dipulihkan.',
            manageBtn: 'Urus',
            downloadBtn: 'Muat turun',
            deleteBtn: 'Padam',
            downloadSentTitle: 'E-mel dihantar!',
            downloadSentDesc: 'Kami akan menghantar e-mel yang mengandungi pautan muat turun data tidak lama lagi.',
            closeBtn: 'Tutup',
            deleteDialogTitle: 'Anda pasti?',
            deleteDialogDesc: 'Tindakan ini akan memadam semua data yang berkaitan dengan bisnes.ai dan melog anda keluar. Jika anda log masuk semula dalam 30 hari, data anda boleh dipulihkan. Selepas 30 hari, data anda akan dipadam secara kekal.',
            deleteEmailPrefix: 'Masukkan e-mel anda ',
            deleteEmailSuffix: ' untuk mengesahkan',
            cancelBtn: 'Batal',
            saveBtn: 'Simpan',
            editNameTitle: 'Edit Nama',
            editNameLabel: 'Nama paparan',
            updateEmailTitle: 'Kemaskini E-mel',
            updateEmailLabel: 'Alamat e-mel baharu',
            deleteMismatch: 'Masukkan e-mel akaun semasa untuk mengesahkan pemadaman.',
            cookiePrefTitle: 'Pusat Keutamaan Privasi',
            cookiePrefDesc: 'Apabila anda melawat mana-mana laman web, ia mungkin menyimpan atau mendapatkan maklumat pada pelayar anda, kebanyakannya dalam bentuk kuki.',
            cookieAllowAll: 'Benarkan Semua',
            cookieManageTitle: 'Urus Pilihan Persetujuan',
            cookieEssential: 'Kuki Yang Diperlukan',
            cookieAlwaysActive: 'Sentiasa Aktif',
            cookieTargeting: 'Kuki Penyasaran',
            cookiePerformance: 'Kuki Prestasi',
            cookieRejectAll: 'Tolak Semua',
            cookieConfirm: 'Sahkan Pilihan Saya',
            continueBtn: 'Teruskan',
            backBtn: 'Kembali',
            verifyBtn: 'Sahkan',
            verifyEmailTitle: 'Sahkan e-mel baharu',
            verifyEmailDesc: 'Kod pengesahan dihantar ke',
            otpLabel: 'Kod pengesahan',
            emailAlreadyUsed: 'Alamat e-mel ini telah digunakan.',
            securityPasswordTitle: 'Log masuk dengan kata laluan',
            securityPasswordDesc: 'Urus kata laluan akaun anda.',
            securitySetPasswordBtn: 'Tetapkan kata laluan',
            securityNewPasswordLabel: 'Kata laluan baharu',
            securitySavePasswordBtn: 'Simpan kata laluan',
            securitySetPasswordSubtitle: 'Isi borang di bawah untuk menukar kata laluan log masuk anda.',
            passwordTooShort: 'Kata laluan mesti sekurang-kurangnya 8 aksara, bermula dengan huruf besar dan mempunyai simbol khas.',
            googleUnlinkConfirm: 'Adakah anda pasti ingin menyahpaut akaun Google anda?',
            googleUnlinkTitle: 'Anda mungkin akan dilog keluar',
            googleUnlinkDesc: 'Menyahpaut kaedah ini mungkin akan mengeluarkan anda daripada akaun anda.',
            googleNotLinked: 'Sila pautkan akaun Google anda dalam Tetapan Akaun sebelum menggunakan penyambung.'
        }
    };

    function getProfileName() {
        const displayName = localStorage.getItem('pepperDisplayName');
        const username = localStorage.getItem('pepperUsername');
        if (displayName) return displayName;
        if (username && username.includes('@')) return username.split('@')[0];
        return username || 'A';
    }

    function colorFromText(text) {
        let hash = 0;
        for (let i = 0; i < text.length; i++) {
            hash = text.charCodeAt(i) + ((hash << 5) - hash);
        }
        return `hsl(${Math.abs(hash % 360)}, 62%, 48%)`;
    }

    function renderAvatar(target) {
        if (!target) return;
        const avatarUrl = localStorage.getItem('pepperAvatar');
        const profileName = getProfileName();
        if (avatarUrl) {
            target.innerHTML = `<img src="${escapeAttr(resolveAvatarSrc(avatarUrl))}" alt="Avatar" referrerpolicy="no-referrer">`;
            target.style.background = 'transparent';
            return;
        }
        const firstLetter = profileName.charAt(0).toUpperCase();
        const bgColor = colorFromText(profileName);
        target.textContent = firstLetter;
        target.style.background = `linear-gradient(135deg, ${bgColor}, #111111)`;
    }

    function syncAccountLanguageLabel() {
        const lang = getPreferredLanguage();
        if (langLabel) langLabel.textContent = languageLabels[lang] || 'EN';
    }

    function accountBusinessLabel(value) {
        const lang = getPreferredLanguage();
        return accountBusinessLabels[lang]?.[value] || value;
    }

    function renderAccountBusinessSelectOptions() {
        const lang = getPreferredLanguage();
        const copy = accountCopy[lang] || accountCopy.en;
        const definitions = [
            [document.getElementById('editBusinessCategory'), accountBusinessCategories, copy.selectBusinessCategory],
            [document.getElementById('editBusinessNature'), accountBusinessNatures, copy.selectBusinessNature],
        ];
        definitions.forEach(([select, values, placeholder]) => {
            if (!select) return;
            const selected = select.value || select.dataset.selectedValue || '';
            select.replaceChildren();
            const emptyOption = document.createElement('option');
            emptyOption.value = '';
            emptyOption.textContent = placeholder;
            emptyOption.disabled = true;
            emptyOption.selected = !selected;
            select.appendChild(emptyOption);
            values.forEach((value) => {
                const option = document.createElement('option');
                option.value = value;
                option.textContent = accountBusinessLabel(value);
                option.selected = value === selected;
                select.appendChild(option);
            });
            select.dataset.selectedValue = selected;
        });
    }

    function renderBusinessProfileValues() {
        const copy = accountCopy[getPreferredLanguage()] || accountCopy.en;
        const category = localStorage.getItem('pepperBusinessCategory') || '';
        const nature = localStorage.getItem('pepperBusinessNature') || '';
        const categoryElement = document.getElementById('accountBusinessCategory');
        const natureElement = document.getElementById('accountBusinessNature');
        if (categoryElement) categoryElement.textContent = category ? accountBusinessLabel(category) : copy.notProvided;
        if (natureElement) natureElement.textContent = nature ? accountBusinessLabel(nature) : copy.notProvided;
    }

    function renderAccountLanguage() {
        const lang = getPreferredLanguage();
        const copy = accountCopy[lang] || accountCopy.en;
        document.querySelectorAll('[data-account-i18n]').forEach(el => {
            const key = el.dataset.accountI18n;
            if (copy[key]) el.textContent = copy[key];
        });
        updateDeletePrompt();
        syncAccountLanguageLabel();
        renderAccountBusinessSelectOptions();
        renderBusinessProfileValues();
    }

    function getAccountEmail() {
        return localStorage.getItem('pepperUsername') || '';
    }

    function updateDeletePrompt() {
        if (!deleteEmailPrompt) return;
        const lang = getPreferredLanguage();
        const copy = accountCopy[lang] || accountCopy.en;
        const email = getAccountEmail();
        const strong = document.createElement('strong');
        strong.textContent = email;
        deleteEmailPrompt.replaceChildren(
            document.createTextNode(copy.deleteEmailPrefix),
            strong,
            document.createTextNode(copy.deleteEmailSuffix),
        );
        if (deleteEmailInput) deleteEmailInput.placeholder = email;
    }

    function syncDeleteConfirmState() {
        if (!deleteConfirmBtn || !deleteEmailInput) return;
        deleteConfirmBtn.disabled = deleteEmailInput.value.trim().toLowerCase() !== getAccountEmail().toLowerCase();
    }

    function openDeleteDialog() {
        if (!deleteDialog) return;
        renderAccountLanguage();
        if (deleteEmailInput) {
            deleteEmailInput.value = '';
            syncDeleteConfirmState();
        }
        deleteDialog.hidden = false;
        setTimeout(() => deleteEmailInput && deleteEmailInput.focus(), 0);
    }

    function closeDeleteDialog() {
        if (deleteDialog) deleteDialog.hidden = true;
    }

    function updateLoginMethodButtons(hasPassword, googleLinked, authProvider) {
        const lang = getPreferredLanguage();
        const copy = accountCopy[lang] || accountCopy.en;
        const emailBtn = document.getElementById('emailMethodBtn');
        const googleBtn = document.getElementById('googleMethodBtn');
        const googleEmailEl = document.getElementById('accountGoogleEmail');

        if (emailBtn) {
            if (hasPassword || authProvider === 'local') {
                emailBtn.textContent = copy.btnEnabled;
                emailBtn.className = 'account-login-method-btn account-login-method-btn--enabled';
            } else {
                emailBtn.textContent = copy.btnDisabled;
                emailBtn.className = 'account-login-method-btn account-login-method-btn--disabled';
            }
        }
        if (googleBtn) {
            const isPrimaryGoogle = authProvider === 'google' && !hasPassword;
            if (googleLinked) {
                googleBtn.textContent = copy.btnEnabled;
                googleBtn.className = 'account-login-method-btn account-login-method-btn--enabled';
                googleBtn.disabled = isPrimaryGoogle;
                googleBtn.style.opacity = isPrimaryGoogle ? '0.5' : '';
                googleBtn.style.cursor = isPrimaryGoogle ? 'not-allowed' : '';
            } else {
                googleBtn.textContent = copy.btnConnect;
                googleBtn.className = 'account-login-method-btn account-login-method-btn--connect';
                googleBtn.disabled = false;
                googleBtn.style.opacity = '';
                googleBtn.style.cursor = '';
            }
        }
        if (googleEmailEl) {
            const gEmail = localStorage.getItem('pepperGoogleEmail');
            googleEmailEl.textContent = gEmail || '—';
        }
    }

    async function renderProfileSection() {
        const name = getProfileName();
        const email = getAccountEmail();
        const profileNameEl = document.getElementById('accountProfileName');
        const profileEmailEl = document.getElementById('accountProfileEmail');
        const profileAvatarEl = document.getElementById('accountProfileAvatar');
        const profileCreatedEl = document.getElementById('accountProfileCreated');
        if (profileNameEl) profileNameEl.textContent = name || 'A';
        if (profileEmailEl) profileEmailEl.textContent = email || '—';
        if (profileCreatedEl) {
            const stored = localStorage.getItem('pepperCreatedAt');
            if (stored) {
                const d = new Date(stored);
                const lang = getPreferredLanguage();
                profileCreatedEl.textContent = d.toLocaleDateString(lang === 'ms' ? 'ms-MY' : 'en-US', { year: 'numeric', month: 'long', day: 'numeric' });
            } else {
                profileCreatedEl.textContent = '—';
            }
        }
        if (profileAvatarEl) renderAvatar(profileAvatarEl);
        renderBusinessProfileValues();

        const token = localStorage.getItem('pepperSession');
        if (token) {
            try {
                const res = await fetch('/api/account/preferences', { headers: { 'Authorization': `Bearer ${token}` } });
                if (res.ok) {
                    const data = await res.json();
                    localStorage.setItem('pepperAuthProvider', data.auth_provider || 'local');
                    localStorage.setItem('pepperGoogleLinked', data.google_linked ? 'true' : 'false');
                    localStorage.setItem('pepperHasPassword', data.has_password ? 'true' : 'false');
                    if (data.google_email) localStorage.setItem('pepperGoogleEmail', data.google_email);
                    else localStorage.removeItem('pepperGoogleEmail');
                    storeAccountProfileFields(data);
                    renderBusinessProfileValues();
                    if (!data.google_linked) clearConnectorChecks();
                    updateLoginMethodButtons(data.has_password, data.google_linked, data.auth_provider);
                }
            } catch (_) {}
        } else {
            updateLoginMethodButtons(
                localStorage.getItem('pepperHasPassword') === 'true',
                localStorage.getItem('pepperGoogleLinked') === 'true',
                localStorage.getItem('pepperAuthProvider') || 'local'
            );
        }
    }

    function switchAccountSection(sectionKey) {
        const sections = {
            profile: document.getElementById('accountProfileSection'),
            security: document.getElementById('accountSecuritySection'),
            data: document.getElementById('accountDataSection')
        };
        Object.values(sections).forEach(s => {
            if (!s) return;
            s.hidden = true;
            s.classList.remove('account-section-enter');
        });
        if (sections[sectionKey]) {
            sections[sectionKey].hidden = false;
            void sections[sectionKey].offsetWidth;
            sections[sectionKey].classList.add('account-section-enter');
            sections[sectionKey].addEventListener('animationend', () => {
                sections[sectionKey]?.classList.remove('account-section-enter');
            }, { once: true });
        }

        overlay.querySelectorAll('.account-nav-item').forEach(btn => {
            const isActive = btn.dataset.accountSection === sectionKey;
            btn.classList.toggle('active', isActive);
            const existing = btn.querySelector('.account-nav-bullet');
            if (isActive && !existing) {
                const bullet = document.createElement('span');
                bullet.className = 'account-nav-bullet';
                btn.insertBefore(bullet, btn.firstChild);
            } else if (!isActive && existing) {
                existing.remove();
            }
        });

        if (sectionKey === 'profile') renderProfileSection();
        if (sectionKey === 'security') {
            const mv = document.getElementById('securityMainView');
            const pv = document.getElementById('securitySetPasswordView');
            if (mv) mv.hidden = false;
            if (pv) pv.hidden = true;
        }
    }

    function openAccountPage() {
        if (!localStorage.getItem('pepperUserId')) {
            window.location.href = '/static/login.html';
            return;
        }
        if (welcomeName) welcomeName.textContent = getProfileName();
        renderAvatar(topAvatar);
        renderAccountLanguage();
        switchAccountSection('profile');
        if (isMobileLayout()) setSidebarCollapsed(true, { skipHistory: true });
        overlay.classList.add('show');
        overlay.setAttribute('aria-hidden', 'false');
        document.body.classList.add('account-page-open');
        markMobileHistoryOverlayOpen('account');
    }

    function closeAccountPageRaw() {
        overlay.classList.remove('show');
        overlay.setAttribute('aria-hidden', 'true');
        document.body.classList.remove('account-page-open');
    }

    function closeAccountPage() {
        if (!overlay.classList.contains('show')) return;
        closeAccountPageRaw();
        markMobileHistoryOverlayClosed('account');
    }

    registerMobileHistoryOverlay('account', closeAccountPageRaw);

    window.addEventListener('open-mof-account-page', openAccountPage);
    if (sessionStorage.getItem('pepperOpenAccountAfterGoogle') === '1') {
        sessionStorage.removeItem('pepperOpenAccountAfterGoogle');
        setTimeout(openAccountPage, 0);
    }

    if (backBtn) backBtn.addEventListener('click', closeAccountPage);

    overlay.querySelectorAll('.account-nav-item[data-account-section]').forEach(btn => {
        btn.addEventListener('click', () => switchAccountSection(btn.dataset.accountSection));
    });

    if (langBtn) {
        langBtn.addEventListener('click', () => {
            const currentLang = getPreferredLanguage();
            const nextLang = languageOrder[(languageOrder.indexOf(currentLang) + 1) % languageOrder.length] || DEFAULT_LANG;
            localStorage.setItem('pepperLang', nextLang);
            if (window.applyPepperLang) window.applyPepperLang(nextLang);
            renderAccountLanguage();
            syncPreferenceControls();
            saveUserPreferences({ language: nextLang });
        });
    }

    if (downloadBtn && downloadDialog) {
        downloadBtn.addEventListener('click', async () => {
            const token = localStorage.getItem('pepperSession');
            if (!token) return;
            downloadBtn.disabled = true;
            try {
                const res = await fetch('/api/account/download-data', {
                    method: 'POST',
                    headers: { 'Authorization': `Bearer ${token}` }
                });
                if (!res.ok) {
                    const d = await res.json().catch(() => ({}));
                    throw new Error(d.detail || 'Failed');
                }
                renderAccountLanguage();
                downloadDialog.hidden = false;
            } catch (err) {
                alert(err.message || 'Failed to send data export');
            } finally {
                downloadBtn.disabled = false;
            }
        });
    }

    if (downloadDialogClose && downloadDialog) {
        downloadDialogClose.addEventListener('click', () => {
            downloadDialog.hidden = true;
        });
    }

    if (downloadDialog) {
        downloadDialog.addEventListener('click', (e) => {
            if (e.target === downloadDialog) downloadDialog.hidden = true;
        });
    }

    if (deleteBtn) {
        deleteBtn.addEventListener('click', openDeleteDialog);
    }

    if (deleteEmailInput) {
        deleteEmailInput.addEventListener('input', syncDeleteConfirmState);
    }

    if (deleteCancelBtn) {
        deleteCancelBtn.addEventListener('click', closeDeleteDialog);
    }

    if (deleteDialog) {
        deleteDialog.addEventListener('click', (e) => {
            if (e.target === deleteDialog) closeDeleteDialog();
        });
    }

    if (deleteConfirmBtn) {
        deleteConfirmBtn.addEventListener('click', async () => {
            const lang = getPreferredLanguage();
            const copy = accountCopy[lang] || accountCopy.en;
            if (deleteEmailInput && deleteEmailInput.value.trim().toLowerCase() !== getAccountEmail().toLowerCase()) {
                alert(copy.deleteMismatch);
                return;
            }
            const token = localStorage.getItem('pepperSession');
            if (!token) {
                window.location.href = '/static/login.html';
                return;
            }
            try {
                deleteConfirmBtn.disabled = true;
                const res = await fetch('/api/account', {
                    method: 'DELETE',
                    headers: { 'Authorization': `Bearer ${token}` }
                });
                if (!res.ok) {
                    const data = await res.json().catch(() => ({}));
                    throw new Error(data.detail || 'Delete failed');
                }
                clearStoredAccountFields();
                window.location.href = '/static/login.html';
            } catch (err) {
                syncDeleteConfirmState();
                alert(err.message || 'Delete failed');
            }
        });
    }

    window.addEventListener('mof-preferences-changed', () => {
        renderAccountLanguage();
    });

    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && overlay.classList.contains('show')) {
            closeAccountPage();
        }
    });

    // ---- Edit Name Dialog ----
    const editNameBtn = document.getElementById('editNameBtn');
    const editNameDialog = document.getElementById('editNameDialog');
    const editNameInput = document.getElementById('editNameInput');
    const editNameError = document.getElementById('editNameError');
    const editNameCancelBtn = document.getElementById('editNameCancelBtn');
    const editNameSaveBtn = document.getElementById('editNameSaveBtn');

    if (editNameBtn && editNameDialog) {
        editNameBtn.addEventListener('click', () => {
            if (editNameInput) editNameInput.value = getProfileName();
            if (editNameError) editNameError.textContent = '';
            editNameDialog.hidden = false;
            setTimeout(() => editNameInput && editNameInput.focus(), 0);
        });
    }
    if (editNameCancelBtn) editNameCancelBtn.addEventListener('click', () => { if (editNameDialog) editNameDialog.hidden = true; });
    if (editNameDialog) editNameDialog.addEventListener('click', e => { if (e.target === editNameDialog) editNameDialog.hidden = true; });

    if (editNameSaveBtn && editNameInput) {
        editNameSaveBtn.addEventListener('click', async () => {
            const newName = editNameInput.value.trim();
            if (!newName) { if (editNameError) editNameError.textContent = '名称不能为空'; return; }
            const token = localStorage.getItem('pepperSession');
            if (!token) return;
            editNameSaveBtn.disabled = true;
            try {
                const res = await fetch('/api/account/profile', {
                    method: 'PUT',
                    headers: { 'Authorization': `Bearer ${token}`, 'Content-Type': 'application/json' },
                    body: JSON.stringify({ display_name: newName })
                });
                const data = await res.json().catch(() => ({}));
                if (!res.ok) throw new Error(data.detail || 'Failed');
                localStorage.setItem('pepperDisplayName', data.display_name);
                if (editNameDialog) editNameDialog.hidden = true;
                renderProfileSection();
                const nameEl = document.getElementById('userDisplayName');
                if (nameEl) nameEl.textContent = data.display_name;
                if (welcomeName) welcomeName.textContent = data.display_name;
            } catch (err) {
                if (editNameError) editNameError.textContent = err.message || 'Failed to update name';
            } finally {
                editNameSaveBtn.disabled = false;
            }
        });
    }

    // ---- Edit Business Profile Dialog ----
    const editBusinessProfileBtn = document.getElementById('editBusinessProfileBtn');
    const editBusinessProfileDialog = document.getElementById('editBusinessProfileDialog');
    const editBusinessCategory = document.getElementById('editBusinessCategory');
    const editBusinessNature = document.getElementById('editBusinessNature');
    const editBusinessProfileError = document.getElementById('editBusinessProfileError');
    const editBusinessProfileCancelBtn = document.getElementById('editBusinessProfileCancelBtn');
    const editBusinessProfileSaveBtn = document.getElementById('editBusinessProfileSaveBtn');

    function closeBusinessProfileDialog() {
        if (editBusinessProfileDialog) editBusinessProfileDialog.hidden = true;
    }

    if (editBusinessProfileBtn && editBusinessProfileDialog) {
        editBusinessProfileBtn.addEventListener('click', () => {
            if (editBusinessProfileError) editBusinessProfileError.textContent = '';
            if (editBusinessCategory) editBusinessCategory.dataset.selectedValue = localStorage.getItem('pepperBusinessCategory') || '';
            if (editBusinessNature) editBusinessNature.dataset.selectedValue = localStorage.getItem('pepperBusinessNature') || '';
            renderAccountBusinessSelectOptions();
            editBusinessProfileDialog.hidden = false;
            setTimeout(() => editBusinessCategory?.focus(), 0);
        });
    }
    editBusinessProfileCancelBtn?.addEventListener('click', closeBusinessProfileDialog);
    editBusinessProfileDialog?.addEventListener('click', (event) => {
        if (event.target === editBusinessProfileDialog) closeBusinessProfileDialog();
    });
    [editBusinessCategory, editBusinessNature].forEach((select) => {
        select?.addEventListener('change', () => {
            select.dataset.selectedValue = select.value;
            if (editBusinessProfileError) editBusinessProfileError.textContent = '';
        });
    });

    editBusinessProfileSaveBtn?.addEventListener('click', async () => {
        const copy = accountCopy[getPreferredLanguage()] || accountCopy.en;
        if (!editBusinessCategory?.value || !editBusinessNature?.value) {
            if (editBusinessProfileError) editBusinessProfileError.textContent = copy.businessProfileRequired;
            return;
        }
        const token = localStorage.getItem('pepperSession');
        if (!token) return;
        editBusinessProfileSaveBtn.disabled = true;
        try {
            const response = await fetch('/api/account/business-profile', {
                method: 'PUT',
                credentials: 'same-origin',
                headers: { 'Authorization': `Bearer ${token}`, 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    business_category: editBusinessCategory.value,
                    business_nature: editBusinessNature.value,
                }),
            });
            const data = await response.json().catch(() => ({}));
            if (!response.ok || data.status !== 'success') {
                throw new Error(data.detail || copy.businessProfileSaveFailed);
            }
            localStorage.setItem('pepperBusinessCategory', data.business_category);
            localStorage.setItem('pepperBusinessNature', data.business_nature);
            localStorage.setItem('pepperRequiresBusinessProfileCompletion', 'false');
            closeBusinessProfileDialog();
            renderBusinessProfileValues();
        } catch (error) {
            if (editBusinessProfileError) editBusinessProfileError.textContent = error.message || copy.businessProfileSaveFailed;
        } finally {
            editBusinessProfileSaveBtn.disabled = false;
        }
    });

    // ---- Edit Email Dialog (multi-step) ----
    const updateEmailBtn = document.getElementById('updateEmailBtn');
    const editEmailDialog = document.getElementById('editEmailDialog');
    const editEmailStep1 = document.getElementById('editEmailStep1');
    const editEmailStep2 = document.getElementById('editEmailStep2');
    const editEmailInput = document.getElementById('editEmailInput');
    const editEmailError = document.getElementById('editEmailError');
    const editEmailCancelBtn = document.getElementById('editEmailCancelBtn');
    const editEmailContinueBtn = document.getElementById('editEmailContinueBtn');
    const editEmailOTPInput = document.getElementById('editEmailOTPInput');
    const editEmailOTPError = document.getElementById('editEmailOTPError');
    const editEmailOTPTarget = document.getElementById('editEmailOTPTarget');
    const editEmailBackBtn = document.getElementById('editEmailBackBtn');
    const editEmailVerifyBtn = document.getElementById('editEmailVerifyBtn');
    let emailChangePendingId = null;

    function openEmailDialog() {
        if (editEmailInput) editEmailInput.value = '';
        if (editEmailError) editEmailError.textContent = '';
        if (editEmailStep1) editEmailStep1.hidden = false;
        if (editEmailStep2) editEmailStep2.hidden = true;
        emailChangePendingId = null;
        if (editEmailDialog) editEmailDialog.hidden = false;
        setTimeout(() => editEmailInput && editEmailInput.focus(), 0);
    }

    if (updateEmailBtn) updateEmailBtn.addEventListener('click', openEmailDialog);
    if (editEmailCancelBtn) editEmailCancelBtn.addEventListener('click', () => { if (editEmailDialog) editEmailDialog.hidden = true; });
    if (editEmailDialog) editEmailDialog.addEventListener('click', e => { if (e.target === editEmailDialog) editEmailDialog.hidden = true; });

    if (editEmailBackBtn) {
        editEmailBackBtn.addEventListener('click', () => {
            if (editEmailStep1) editEmailStep1.hidden = false;
            if (editEmailStep2) editEmailStep2.hidden = true;
            if (editEmailOTPError) editEmailOTPError.textContent = '';
            if (editEmailOTPInput) editEmailOTPInput.value = '';
        });
    }

    if (editEmailContinueBtn) {
        editEmailContinueBtn.addEventListener('click', async () => {
            const lang = getPreferredLanguage();
            const copy = accountCopy[lang] || accountCopy.en;
            const newEmail = editEmailInput ? editEmailInput.value.trim() : '';
            if (!newEmail || !newEmail.includes('@')) {
                if (editEmailError) editEmailError.textContent = copy.updateEmailLabel || '请输入有效的邮箱地址';
                return;
            }
            const token = localStorage.getItem('pepperSession');
            if (!token) return;
            editEmailContinueBtn.disabled = true;
            if (editEmailError) editEmailError.textContent = '';
            try {
                const res = await fetch('/api/account/send-email-otp', {
                    method: 'POST',
                    headers: { 'Authorization': `Bearer ${token}`, 'Content-Type': 'application/json' },
                    body: JSON.stringify({ new_email: newEmail })
                });
                const data = await res.json().catch(() => ({}));
                if (res.status === 409) {
                    if (editEmailError) editEmailError.textContent = copy.emailAlreadyUsed || '该邮箱已被使用';
                    return;
                }
                if (!res.ok) throw new Error(data.detail || 'Failed');
                emailChangePendingId = data.pending_id;
                if (editEmailOTPTarget) editEmailOTPTarget.textContent = newEmail;
                if (editEmailStep1) editEmailStep1.hidden = true;
                if (editEmailStep2) editEmailStep2.hidden = false;
                if (editEmailOTPInput) editEmailOTPInput.value = '';
                if (editEmailOTPError) editEmailOTPError.textContent = '';
                setTimeout(() => editEmailOTPInput && editEmailOTPInput.focus(), 0);
            } catch (err) {
                if (editEmailError) editEmailError.textContent = err.message || '发送失败';
            } finally {
                editEmailContinueBtn.disabled = false;
            }
        });
    }

    if (editEmailVerifyBtn) {
        editEmailVerifyBtn.addEventListener('click', async () => {
            const otp = editEmailOTPInput ? editEmailOTPInput.value.trim() : '';
            if (!otp || otp.length !== 6) {
                if (editEmailOTPError) editEmailOTPError.textContent = '请输入6位验证码';
                return;
            }
            if (!emailChangePendingId) return;
            const token = localStorage.getItem('pepperSession');
            if (!token) return;
            editEmailVerifyBtn.disabled = true;
            if (editEmailOTPError) editEmailOTPError.textContent = '';
            try {
                const res = await fetch('/api/account/email', {
                    method: 'PUT',
                    headers: { 'Authorization': `Bearer ${token}`, 'Content-Type': 'application/json' },
                    body: JSON.stringify({ pending_id: emailChangePendingId, otp })
                });
                const data = await res.json().catch(() => ({}));
                if (!res.ok) throw new Error(data.detail || 'Failed');
                localStorage.setItem('pepperUsername', data.username);
                if (editEmailDialog) editEmailDialog.hidden = true;
                renderProfileSection();
                const emailEl = document.getElementById('userEmailDisplay');
                if (emailEl) emailEl.textContent = data.username;
            } catch (err) {
                if (editEmailOTPError) editEmailOTPError.textContent = err.message || '验证失败';
            } finally {
                editEmailVerifyBtn.disabled = false;
            }
        });
    }

    // ---- Cookie Preference Dialog ----
    const cookieManageBtn = document.getElementById('accountCookieManageBtn');
    const cookieDialog = document.getElementById('cookiePrefsDialog');
    const cookieCloseBtn = document.getElementById('cookiePrefsCloseBtn');
    const cookieAllowAllBtn = document.getElementById('cookieAllowAllBtn');
    const cookieRejectAllBtn = document.getElementById('cookieRejectAllBtn');
    const cookieConfirmBtn = document.getElementById('cookieConfirmBtn');
    const cookieTargetingToggle = document.getElementById('cookieTargetingToggle');
    const cookiePerformanceToggle = document.getElementById('cookiePerformanceToggle');

    function loadCookiePrefs() {
        const prefs = JSON.parse(localStorage.getItem('mofCookiePrefs') || '{}');
        if (cookieTargetingToggle) cookieTargetingToggle.checked = !!prefs.targeting;
        if (cookiePerformanceToggle) cookiePerformanceToggle.checked = !!prefs.performance;
    }

    function saveCookiePrefs(targeting, performance) {
        localStorage.setItem('mofCookiePrefs', JSON.stringify({ targeting, performance }));
    }

    function openCookieDialog() {
        loadCookiePrefs();
        if (cookieDialog) cookieDialog.hidden = false;
    }

    function closeCookieDialog() {
        if (cookieDialog) cookieDialog.hidden = true;
    }

    if (cookieManageBtn) cookieManageBtn.addEventListener('click', openCookieDialog);
    if (cookieCloseBtn) cookieCloseBtn.addEventListener('click', closeCookieDialog);

    if (cookieAllowAllBtn) {
        cookieAllowAllBtn.addEventListener('click', () => {
            if (cookieTargetingToggle) cookieTargetingToggle.checked = true;
            if (cookiePerformanceToggle) cookiePerformanceToggle.checked = true;
            saveCookiePrefs(true, true);
            closeCookieDialog();
        });
    }

    if (cookieRejectAllBtn) {
        cookieRejectAllBtn.addEventListener('click', () => {
            if (cookieTargetingToggle) cookieTargetingToggle.checked = false;
            if (cookiePerformanceToggle) cookiePerformanceToggle.checked = false;
            saveCookiePrefs(false, false);
            closeCookieDialog();
        });
    }

    if (cookieConfirmBtn) {
        cookieConfirmBtn.addEventListener('click', () => {
            const targeting = cookieTargetingToggle ? cookieTargetingToggle.checked : false;
            const performance = cookiePerformanceToggle ? cookiePerformanceToggle.checked : false;
            saveCookiePrefs(targeting, performance);
            closeCookieDialog();
        });
    }

    if (cookieDialog) {
        cookieDialog.addEventListener('click', (e) => {
            if (e.target === cookieDialog) closeCookieDialog();
        });
    }

    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && cookieDialog && !cookieDialog.hidden) {
            closeCookieDialog();
        }
    });

    // ---- Security Section: Set Password ----
    const setPasswordBtn = document.getElementById('setPasswordBtn');
    const securityMainView = document.getElementById('securityMainView');
    const securitySetPasswordView = document.getElementById('securitySetPasswordView');
    const securityBreadcrumbBack = document.getElementById('securityBreadcrumbBack');
    const newPasswordInput = document.getElementById('newPasswordInput');
    const newPasswordError = document.getElementById('newPasswordError');
    const newPasswordVisibilityBtn = document.getElementById('newPasswordVisibilityBtn');
    const newPasswordVisibilityIcon = document.getElementById('newPasswordVisibilityIcon');
    const setPasswordCancelBtn = document.getElementById('setPasswordCancelBtn');
    const setPasswordSaveBtn = document.getElementById('setPasswordSaveBtn');

    function showSetPasswordView() {
        if (securityMainView) securityMainView.hidden = true;
        if (securitySetPasswordView) securitySetPasswordView.hidden = false;
        if (newPasswordInput) { newPasswordInput.value = ''; setTimeout(() => newPasswordInput.focus(), 0); }
        if (newPasswordError) newPasswordError.textContent = '';
    }

    function hideSetPasswordView() {
        if (securityMainView) securityMainView.hidden = false;
        if (securitySetPasswordView) securitySetPasswordView.hidden = true;
    }

    if (setPasswordBtn) setPasswordBtn.addEventListener('click', showSetPasswordView);
    if (securityBreadcrumbBack) securityBreadcrumbBack.addEventListener('click', hideSetPasswordView);
    if (setPasswordCancelBtn) setPasswordCancelBtn.addEventListener('click', hideSetPasswordView);

    if (newPasswordVisibilityBtn && newPasswordInput) {
        newPasswordVisibilityBtn.addEventListener('click', () => {
            const isHidden = newPasswordInput.type === 'password';
            newPasswordInput.type = isHidden ? 'text' : 'password';
            if (newPasswordVisibilityIcon) newPasswordVisibilityIcon.className = isHidden ? 'fa-regular fa-eye' : 'fa-regular fa-eye-slash';
        });
    }

    if (setPasswordSaveBtn && newPasswordInput) {
        setPasswordSaveBtn.addEventListener('click', async () => {
            const lang = getPreferredLanguage();
            const copy = accountCopy[lang] || accountCopy.en;
            const pw = newPasswordInput.value;
            if (!passwordMeetsPolicy(pw)) {
                if (newPasswordError) newPasswordError.textContent = copy.passwordTooShort;
                return;
            }
            const token = localStorage.getItem('pepperSession');
            if (!token) return;
            setPasswordSaveBtn.disabled = true;
            if (newPasswordError) newPasswordError.textContent = '';
            try {
                const res = await fetch('/api/account/password', {
                    method: 'PUT',
                    headers: { 'Authorization': `Bearer ${token}`, 'Content-Type': 'application/json' },
                    body: JSON.stringify({ new_password: pw })
                });
                const data = await res.json().catch(() => ({}));
                if (!res.ok) throw new Error(data.detail || 'Failed');
                localStorage.setItem('pepperHasPassword', 'true');
                hideSetPasswordView();
                renderProfileSection();
            } catch (err) {
                if (newPasswordError) newPasswordError.textContent = err.message || '保存失败';
            } finally {
                setPasswordSaveBtn.disabled = false;
            }
        });
    }

    // ---- Google Link / Unlink ----
    async function initGoogleLinkClient() {
        await publicConfigReady;
        const token = localStorage.getItem('pepperSession');
        if (!token) {
            window.location.href = '/static/login.html';
            return;
        }
        startGoogleAccessTokenRedirect('link', 'openid email profile');
    }

    // ---- Google Unlink Confirmation Dialog ----
    const googleUnlinkDialog = document.getElementById('googleUnlinkDialog');
    const googleUnlinkCancelBtn = document.getElementById('googleUnlinkCancelBtn');
    const googleUnlinkProceedBtn = document.getElementById('googleUnlinkProceedBtn');
    let _googleUnlinkResolve = null;

    function openGoogleUnlinkDialog() {
        return new Promise(resolve => {
            _googleUnlinkResolve = resolve;
            if (googleUnlinkDialog) googleUnlinkDialog.hidden = false;
        });
    }
    if (googleUnlinkCancelBtn) googleUnlinkCancelBtn.addEventListener('click', () => {
        if (googleUnlinkDialog) googleUnlinkDialog.hidden = true;
        if (_googleUnlinkResolve) { _googleUnlinkResolve(false); _googleUnlinkResolve = null; }
    });
    if (googleUnlinkProceedBtn) googleUnlinkProceedBtn.addEventListener('click', () => {
        if (googleUnlinkDialog) googleUnlinkDialog.hidden = true;
        if (_googleUnlinkResolve) { _googleUnlinkResolve(true); _googleUnlinkResolve = null; }
    });
    if (googleUnlinkDialog) googleUnlinkDialog.addEventListener('click', e => {
        if (e.target === googleUnlinkDialog) {
            googleUnlinkDialog.hidden = true;
            if (_googleUnlinkResolve) { _googleUnlinkResolve(false); _googleUnlinkResolve = null; }
        }
    });

    const googleMethodBtn = document.getElementById('googleMethodBtn');
    if (googleMethodBtn) {
        googleMethodBtn.addEventListener('click', async () => {
            if (googleMethodBtn.disabled) return;
            const googleLinked = localStorage.getItem('pepperGoogleLinked') === 'true';
            if (googleLinked) {
                const confirmed = await openGoogleUnlinkDialog();
                if (!confirmed) return;
                const token = localStorage.getItem('pepperSession');
                if (!token) return;
                try {
                    const res = await fetch('/api/account/unlink-google', {
                        method: 'POST',
                        headers: { 'Authorization': `Bearer ${token}` }
                    });
                    const data = await res.json().catch(() => ({}));
                    if (!res.ok) throw new Error(data.detail || 'Failed');
                    localStorage.setItem('pepperGoogleLinked', 'false');
                    localStorage.setItem('pepperAuthProvider', 'local');
                    localStorage.removeItem('pepperGoogleEmail');
                    clearConnectorChecks();
                    renderProfileSection();
                } catch (err) {
                    alert(err.message);
                }
            } else {
                initGoogleLinkClient();
            }
        });
    }
})();
