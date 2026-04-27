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
let currentAbortController = null;
let isPaused = false;
let pausedMsgIndex = null;
let guestLoginPromptForced = false;
let lastGuestPromptPulseAt = 0;

const GUEST_QUESTION_LIMIT = 2;
const agentModeBtn = document.getElementById('agentModeBtn');
const guestLimitBanner = document.getElementById('guestLimitBanner');
const guestLimitBannerText = document.getElementById('guestLimitBannerText');
const guestLimitLoginBtn = document.getElementById('guestLimitLoginBtn');
const guestLimitRegisterBtn = document.getElementById('guestLimitRegisterBtn');
const appContainer = document.querySelector('.app-container');
const inputWrapper = document.querySelector('.input-wrapper');
const liquidGlassInput = document.querySelector('.liquid-glass-input');
const COMPOSER_MAX_HEIGHT = 168;
let googleOAuthClientId = '685645444928-ivt7lgsjiatv0ff0r68ckmbln1rdrrm4.apps.googleusercontent.com';

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

async function loadPublicConfig() {
    try {
        const res = await fetch('/api/public-config', { cache: 'no-store' });
        if (!res.ok) return;
        const data = await res.json();
        if (data.google_oauth_client_id) {
            googleOAuthClientId = data.google_oauth_client_id;
        }
    } catch (err) {
        console.warn('Failed to load public config', err);
    }
}
const publicConfigReady = loadPublicConfig();

function resizeComposer() {
    if (!userInput) return;
    userInput.style.height = 'auto';
    const nextHeight = Math.min(userInput.scrollHeight, COMPOSER_MAX_HEIGHT);
    userInput.style.height = `${nextHeight}px`;
    userInput.style.overflowY = userInput.scrollHeight > COMPOSER_MAX_HEIGHT ? 'auto' : 'hidden';
    if (liquidGlassInput) {
        liquidGlassInput.classList.toggle('composer-expanded', nextHeight > 34);
    }
}

function getGuestQuestionCount() {
    return Number(localStorage.getItem('pepperGuestQuestionCount') || '0');
}

function setGuestQuestionCount(count) {
    localStorage.setItem('pepperGuestQuestionCount', String(Math.max(0, count)));
}

function incrementGuestQuestionCount() {
    const next = getGuestQuestionCount() + 1;
    setGuestQuestionCount(next);
    return next;
}

function getUiCopy() {
    return window._pepperLang || {
        greeting: 'How can I help you today?',
        loginBtn: 'Login',
        registerBtn: 'Register',
        guestLimitText: 'Get smarter responses, upload files and images, and unlock more features.',
        guestLimitRegisterBtn: 'Sign up for free',
        agentRequiresLogin: 'Login required for Agent mode'
    };
}

function getNormalLandingMarkup() {
    const copy = getUiCopy();
    return `<h2><span class="logo-text"><i class="fa-solid fa-leaf"></i> Ministry of Finance</span><br/>${copy.greeting || 'How can I help you today?'}</h2>`;
}

function updateGuestLimitBannerCopy() {
    const t = getUiCopy();
    if (guestLimitBannerText) guestLimitBannerText.textContent = t.guestLimitText;
    if (guestLimitLoginBtn) guestLimitLoginBtn.textContent = t.loginBtn;
    if (guestLimitRegisterBtn) guestLimitRegisterBtn.textContent = t.guestLimitRegisterBtn || t.registerBtn;
    if (agentModeBtn) agentModeBtn.title = currentUserId ? 'AI Agent' : (t.agentRequiresLogin || 'Login required for Agent mode');
}

function syncPreferenceControls() {
    const theme = localStorage.getItem('pepperTheme') || 'dark';
    const lang = localStorage.getItem('pepperLang') || 'en';
    document.querySelectorAll('.theme-btn').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.theme === theme);
    });
    document.querySelectorAll('.lang-btn').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.lang === lang);
    });
    window.dispatchEvent(new CustomEvent('mof-preferences-changed', {
        detail: { theme, language: lang }
    }));
}

function applyStoredPreferences(preferences = {}) {
    if (preferences.language) localStorage.setItem('pepperLang', preferences.language);
    if (preferences.theme) localStorage.setItem('pepperTheme', preferences.theme);
    const theme = localStorage.getItem('pepperTheme') || 'dark';
    const lang = localStorage.getItem('pepperLang') || 'en';
    if (window.applyPepperTheme) {
        window.applyPepperTheme(theme);
    } else if (theme === 'dark') {
        document.documentElement.setAttribute('data-theme', 'dark');
    } else {
        document.documentElement.removeAttribute('data-theme');
    }
    if (window.applyPepperLang) window.applyPepperLang(lang);
    syncPreferenceControls();
}

async function saveUserPreferences(partial = {}) {
    const token = localStorage.getItem('pepperJwt');
    if (!token) return;
    const body = {
        language: localStorage.getItem('pepperLang') || 'en',
        theme: localStorage.getItem('pepperTheme') || 'dark',
        ...partial
    };
    try {
        const res = await fetch('/api/account/preferences', {
            method: 'PUT',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${token}`
            },
            body: JSON.stringify(body)
        });
        if (res.ok) {
            const data = await res.json();
            if (data.preferences) applyStoredPreferences(data.preferences);
        }
    } catch (err) {
        console.warn('Failed to save user preferences', err);
    }
}

async function loadUserPreferences() {
    const token = localStorage.getItem('pepperJwt');
    if (!token) return;
    try {
        const res = await fetch('/api/account/preferences', {
            headers: { 'Authorization': `Bearer ${token}` }
        });
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
        if (data.preferences) applyStoredPreferences(data.preferences);
        if (data.google_linked) fetchConnectorsStatus();
        else clearConnectorChecks();
        const sidebarName = document.getElementById('userDisplayName');
        if (sidebarName && data.display_name) sidebarName.textContent = data.display_name;
    } catch (err) {
        console.warn('Failed to load user preferences', err);
    }
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
    toast.textContent = message;
    stack.appendChild(toast);
    requestAnimationFrame(() => toast.classList.add('show'));
    setTimeout(() => {
        toast.classList.remove('show');
        setTimeout(() => toast.remove(), 220);
    }, 3400);
}
window.showToast = showToast;

function updateGuestInputUi() {
    const shouldHideNormalInput = !currentUserId && !isAgentMode && getGuestQuestionCount() >= GUEST_QUESTION_LIMIT;
    const shouldLockGuestAgent = !currentUserId && isAgentMode;

    if (inputWrapper) {
        inputWrapper.classList.toggle('guest-input-hidden', shouldHideNormalInput);
    }
    if (appContainer) {
        appContainer.classList.toggle('guest-input-hidden', shouldHideNormalInput);
    }
    if (liquidGlassInput) {
        liquidGlassInput.classList.toggle('guest-agent-locked', shouldLockGuestAgent);
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

    if (liquidGlassInput) {
        liquidGlassInput.classList.remove('lock-tap');
        void liquidGlassInput.offsetWidth;
        liquidGlassInput.classList.add('lock-tap');
        liquidGlassInput.addEventListener('animationend', () => {
            liquidGlassInput.classList.remove('lock-tap');
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

function syncGuestAccessState() {
    currentUserId = localStorage.getItem('pepperUserId') || null;
    currentUsername = localStorage.getItem('pepperUsername') || null;
    updateGuestLimitBannerCopy();
    if (agentModeBtn) agentModeBtn.classList.toggle('requires-login', !currentUserId);

    if (currentUserId) {
        setGuestQuestionCount(0);
        hideGuestLoginPrompt(true);
        return;
    }

    if (guestLoginPromptForced || getGuestQuestionCount() >= GUEST_QUESTION_LIMIT) {
        showGuestLoginPrompt(false);
    } else {
        hideGuestLoginPrompt(false);
    }
    updateGuestInputUi();
}

if (guestLimitLoginBtn) {
    guestLimitLoginBtn.addEventListener('click', () => {
        window.location.href = '/static/login.html';
    });
}

if (guestLimitRegisterBtn) {
    guestLimitRegisterBtn.addEventListener('click', () => {
        window.location.href = '/static/register.html';
    });
}

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

if (sidebarCloseBtn) sidebarCloseBtn.addEventListener('click', () => pageWrapper.classList.add('sidebar-collapsed'));
if (sidebarOpenBtn) sidebarOpenBtn.addEventListener('click', () => pageWrapper.classList.remove('sidebar-collapsed'));
if (sidebarExpandBtn) sidebarExpandBtn.addEventListener('click', () => pageWrapper.classList.remove('sidebar-collapsed'));

// ============ History ============
const historyToggleBtn = document.getElementById('historyToggleBtn');
const historyList = document.getElementById('historyList');

if (historyToggleBtn && historyList) {
    historyToggleBtn.addEventListener('click', () => {
        historyToggleBtn.classList.toggle('collapsed');
        historyList.classList.toggle('collapsed');
    });
}

async function loadHistory() {
    if (!currentUserId) {
        const hl = document.getElementById('historyList');
        if(hl) hl.innerHTML = '<li class="history-placeholder">Please login to see history</li>';
        return;
    }
    try {
        const res = await fetch(`/api/history?user_id=${currentUserId}`);
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
                    delBtn.innerHTML = '<i class="fa-regular fa-trash-can"></i>';
                    delBtn.title = "Delete Chat";
                    delBtn.onclick = async (e) => {
                        e.stopPropagation();
                        li.style.transition = 'all 0.3s cubic-bezier(0.16, 1, 0.3, 1)';
                        li.style.opacity = '0';
                        li.style.transform = 'translateX(-20px)';
                        
                        setTimeout(async () => {
                            try {
                                const dr = await fetch(`/api/history/${chat._id}`, { method: 'DELETE' });
                                if (dr.ok) {
                                    if (currentChatId === chat._id) {
                                        document.getElementById('newChatBtn').click();
                                    }
                                    loadHistory();
                                }
                            } catch(err) { console.error("Failed to delete", err); }
                        }, 300);
                    };
                    
                    li.onclick = () => {
                        document.querySelectorAll('.history-item').forEach(item => {
                            item.classList.toggle('active', item.dataset.chatId === chat._id);
                        });
                        loadChat(chat._id);
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

async function loadChat(chatId) {
    if (isGenerating) return;
    try {
        const historyUrl = currentUserId
            ? `/api/history/${chatId}?user_id=${encodeURIComponent(currentUserId)}`
            : `/api/history/${chatId}`;
        const res = await fetch(historyUrl);
        const data = await res.json();
        if(data.chat) {
            currentChatId = chatId;
            messagesContainer.innerHTML = '';
            logoContainer.style.display = 'none';
            document.querySelector('.app-container').classList.remove('centered-landing');
            isFirstMessage = false;
            
            // Restore agent mode / normal mode based on saved flag
            const wasAgentMode = !!data.chat.agent_mode;
            isAgentMode = wasAgentMode;
            
            document.querySelectorAll('.nav-menu-btn').forEach(btn => btn.classList.remove('active'));
            
            if (wasAgentMode) {
                document.getElementById('agentModeBtn').classList.add('active');
                document.getElementById('uploadBtn').style.display = 'inline-flex';
                document.querySelector('.toggles-container').style.display = 'none';
                const cc = document.getElementById('connectorsContainer');
                if (cc) cc.style.display = 'flex';
            } else {
                document.getElementById('newChatBtn').classList.add('active');
                document.getElementById('uploadBtn').style.display = 'none';
                document.querySelector('.toggles-container').style.display = '';
                const cc = document.getElementById('connectorsContainer');
                if (cc) cc.style.display = 'none';
            }
            
            chatMessages = data.chat.messages || [];
            let feedbacks = data.chat.feedback || {};
            chatMessages.forEach((msg, idx) => {
                appendMessage(msg.content, msg.role, msg, idx, feedbacks[idx.toString()] || 0, true);
            });
            setTimeout(() => {
                const chatArea = document.getElementById('chatArea');
                chatArea.scrollTo({ top: 0, behavior: 'smooth' });
            }, 50);
        }
    } catch (e) { console.error("Failed to load chat", e); }
}

function openFreshNormalChat() {
    isAgentMode = false;
    document.getElementById('uploadBtn').style.display = 'none';
    // Restore toggles in normal chat mode
    document.querySelector('.toggles-container').style.display = '';
    const connectorsContainer = document.getElementById('connectorsContainer');
    if (connectorsContainer) connectorsContainer.style.display = 'none';
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
    if (!currentUserId && getGuestQuestionCount() < GUEST_QUESTION_LIMIT) {
        guestLoginPromptForced = false;
    }
    syncGuestAccessState();
}

function openFreshAgentChat(showLoginPrompt = false) {
    isAgentMode = true;
    document.getElementById('uploadBtn').style.display = 'inline-flex';
    // Agent mode: hide think/web toggles, show connectors
    document.querySelector('.toggles-container').style.display = 'none';
    const connectorsContainer = document.getElementById('connectorsContainer');
    if (connectorsContainer) connectorsContainer.style.display = 'flex';
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

if (liquidGlassInput) {
    const lockedGuestAgentHandler = (e) => {
        if (!currentUserId && isAgentMode) {
            e.preventDefault();
            e.stopPropagation();
            pulseGuestLoginPrompt();
        }
    };
    liquidGlassInput.addEventListener('pointerdown', lockedGuestAgentHandler, true);
    liquidGlassInput.addEventListener('focusin', lockedGuestAgentHandler, true);
}

// ============ Toggles ============
function updateTogglesUI() {
    thinkToggle.classList.toggle('active', isThinkMode);
    webToggle.classList.toggle('active', isWebMode);
}
updateTogglesUI();
thinkToggle.addEventListener('click', () => { isThinkMode = !isThinkMode; updateTogglesUI(); });
webToggle.addEventListener('click', () => { isWebMode = !isWebMode; updateTogglesUI(); });

// ============ Connectors ============
const connectorBtn = document.getElementById('connectorBtn');
const connectorsContainer = document.getElementById('connectorsContainer');
if (connectorBtn && connectorsContainer) {
    connectorBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        connectorsContainer.classList.toggle('open');
    });
    
    document.addEventListener('click', (e) => {
        if (!connectorsContainer.contains(e.target)) {
            connectorsContainer.classList.remove('open');
        }
    });
}

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
    const token = localStorage.getItem('pepperJwt');
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

function initGoogleClients() {
    if (typeof google === 'undefined') {
        setTimeout(initGoogleClients, 500);
        return;
    }
    const loginHint = localStorage.getItem('pepperUsername');
    
    Object.keys(SCOPE_MAP).forEach(service => {
        let config = {
            client_id: googleOAuthClientId,
            scope: SCOPE_MAP[service],
            include_granted_scopes: false,  // CRITICAL: Force strictly separate scope prompts
            ux_mode: 'popup',
            callback: async (response) => {
            if (response && response.code) {
                try {
                        const jwt = localStorage.getItem('pepperJwt');
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
                                redirect_uri: 'postmessage',
                                service_id: pendingOAuthService
                            })
                        });
                        
                        if (res.status === 401) {
                            localStorage.removeItem('pepperJwt');
                            localStorage.removeItem('pepperUserId');
                            localStorage.removeItem('pepperDisplayName');
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
                            // Only pop up if there is an error
                            showToast("Connector Auth Failed: " + (data.detail || data.message || "Unknown error"), true);
                            
                            // Revert toggle UI immediately visually
                            const checkbox = document.getElementById(`checkbox-${pendingOAuthService}`);
                            if(checkbox) checkbox.checked = false;
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
        
        googleClients[service] = google.accounts.oauth2.initCodeClient(config);
    });
}

const SCOPE_MAP = {
    'drive': 'https://www.googleapis.com/auth/drive.file',
    'gmail': 'https://www.googleapis.com/auth/gmail.send',
    'docs': 'https://www.googleapis.com/auth/documents',
    'calendar': 'https://www.googleapis.com/auth/calendar.events',
    'meet': 'https://www.googleapis.com/auth/calendar.events'
};

// When a switch is clicked, trigger Google OAuth with granular scope OR toggle state
document.querySelectorAll('.liquid-glass-switch').forEach(switchLabel => {
    switchLabel.addEventListener('click', async (e) => {
        // Stop bubbling and native checkbox toggling! We control this!
        e.preventDefault();
        e.stopPropagation();
        
        const token = localStorage.getItem('pepperJwt');
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
                const _lang = localStorage.getItem('pepperLang') || 'en';
                const _msgs = { zh: '请先在账户设置中绑定您的 Google 账号后再使用连接器。', en: 'Please link your Google account in Account Settings before using connectors.', ms: 'Sila pautkan akaun Google anda dalam Tetapan Akaun sebelum menggunakan penyambung.' };
                showToast(_msgs[_lang] || _msgs.en, true);
                return;
            }
            // Turning ON always requires Google Auth window to retrieve/confirm scope
            if (!googleClients[service]) {
                showToast("Google OAuth loading... please wait.", true);
                return;
            }
            if (connectorsContainer) connectorsContainer.classList.remove('open');
            pendingOAuthService = service;
            // Pre-initialized client fires synchronously, browsers won't block popup
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
function scrollToBottom() {
    const chatArea = document.getElementById('chatArea');
    chatArea.scrollTo({ top: chatArea.scrollHeight, behavior: 'smooth' });
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

// Copy button handler
function copyCodeBlock(btn) {
    const pre = btn.closest('.code-block-wrapper').querySelector('pre');
    const text = pre.innerText || pre.textContent;
    navigator.clipboard.writeText(text).then(() => {
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
    if (typeof marked === 'undefined') return text.replace(/\n/g, '<br>');
    let html = marked.parse(text);
    // Post-process: make ALL links open in new tab (no renderer API dependency)
    html = html.replace(/<a\s+href=/g, '<a class="md-link" target="_blank" rel="noopener noreferrer" href=');
    // Price badges
    html = highlightPrices(html);
    return html;
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
            <span class="code-lang-label">${lang}</span>
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
    actions.className = 'msg-action-bar';
    
    const copyBtn = document.createElement('button');
    copyBtn.className = 'msg-action-btn';
    copyBtn.title = 'Copy';
    copyBtn.innerHTML = '<i class="fa-regular fa-copy"></i>';
    copyBtn.onclick = () => {
        navigator.clipboard.writeText(msgText);
        copyBtn.innerHTML = '<i class="fa-solid fa-check"></i>';
        setTimeout(() => { copyBtn.innerHTML = '<i class="fa-regular fa-copy"></i>'; }, 1500);
    };
    actions.appendChild(copyBtn);

    if (isAssistant) {
        const regenBtn = document.createElement('button');
        regenBtn.className = 'msg-action-btn';
        regenBtn.title = 'Regenerate';
        regenBtn.innerHTML = '<i class="fa-solid fa-rotate-right"></i>';
        regenBtn.onclick = () => {
            if (isGenerating) return;
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
            
            handleSend();
        };
        actions.appendChild(regenBtn);

        const resumeBtn = document.createElement('button');
        resumeBtn.className = 'msg-action-btn';
        resumeBtn.title = 'Resume Generation';
        resumeBtn.innerHTML = '<i class="fa-solid fa-play"></i>';
        resumeBtn.onclick = () => {
            if (isGenerating) return;
            handleSend(true, msgIndex);
        };
        actions.appendChild(resumeBtn);

        const likeBtn = document.createElement('button');
        likeBtn.className = `msg-action-btn ${feedbackVal === 1 ? 'active' : ''}`;
        likeBtn.innerHTML = '<i class="fa-regular fa-thumbs-up"></i>';
        
        const dislikeBtn = document.createElement('button');
        dislikeBtn.className = `msg-action-btn ${feedbackVal === -1 ? 'active' : ''}`;
        dislikeBtn.innerHTML = '<i class="fa-regular fa-thumbs-down"></i>';

        const sendFeedback = async (val) => {
            let newVal = ((likeBtn.classList.contains('active') && val === 1) || (dislikeBtn.classList.contains('active') && val === -1)) ? 0 : val;
            likeBtn.classList.toggle('active', newVal === 1);
            dislikeBtn.classList.toggle('active', newVal === -1);
            if (currentChatId) {
                try {
                    await fetch('/api/chat/feedback', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
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
        editBtn.title = 'Edit & Resend';
        editBtn.innerHTML = '<i class="fa-solid fa-pen"></i>';
        editBtn.onclick = () => {
            if (isGenerating) return;
            const input = document.createElement('input');
            input.type = 'text';
            input.className = 'edit-input';
            input.value = chatMessages[msgIndex].content;
            
            const userBubble = wrapper.querySelector('.message-bubble.user');
            userBubble.style.display = 'none';
            wrapper.insertBefore(input, actions);
            input.focus();
            let edited = false;
            
            const submitEdit = () => {
                if (edited) return;
                edited = true;
                const newText = input.value.trim();
                input.remove();
                userBubble.style.display = '';
                
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
            input.addEventListener('keydown', (e) => { if (e.key === 'Enter') submitEdit(); });
            input.addEventListener('blur', submitEdit);
        };
        actions.appendChild(editBtn);
    }
    
    wrapper.appendChild(actions);
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
        wrapper.appendChild(bubble);
        
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
                            const favUrl = getFavicon(s.url);
                            return `
                                <a class="source-card" href="${s.url}" target="_blank" rel="noopener noreferrer">
                                    ${favUrl ? `<img class="source-card-favicon" src="${favUrl}" alt="">` : ''}
                                    <div class="source-card-text">
                                        <span class="source-card-title">${s.title || domain}</span>
                                        <span class="source-card-domain">${domain}</span>
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
            if (displayAnswer.includes('[GMAIL_CONFIRM_PENDING]')) {
                displayAnswer = displayAnswer.replace('[GMAIL_CONFIRM_PENDING]', '');
                hasGmailPending = true;
            }
            aDiv.innerHTML = renderMd(displayAnswer);
            wrapper.appendChild(aDiv);
            
            if (hasGmailPending) {
                const gmailCard = document.createElement('div');
                gmailCard.className = 'gmail-confirm-card';
                gmailCard.style = "margin-top: 12px; padding-top: 12px; border-top: 1px solid var(--outline-variant);";
                gmailCard.innerHTML = `
                    <div class="gmail-card-actions" style="display: flex; gap: 10px; align-items: center; margin-bottom: 8px;">
                        <button class="gmail-confirm-btn" id="gmailConfirm_${Date.now()}" style="padding: 8px 16px; border-radius: 8px; font-weight: 500; font-size: 0.9em; display: inline-flex; align-items: center; gap: 8px; cursor: pointer; transition: all 0.2s ease; border: none; background: #6366f1; color: white; min-width: 130px; justify-content: center;">
                            <i class="fa-solid fa-paper-plane"></i> Confirm Send
                        </button>
                        <button class="gmail-cancel-btn" id="gmailCancel_${Date.now()}" style="padding: 8px 16px; border-radius: 8px; font-weight: 500; font-size: 0.9em; display: inline-flex; align-items: center; gap: 8px; cursor: pointer; transition: all 0.2s ease; border: 1px solid var(--outline-variant); background: transparent; color: var(--text-color); min-width: 100px; justify-content: center;">
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
                    confirmBtn.style.background = '#6366f1';
                    const fakeInput = document.getElementById('userInput');
                    if (fakeInput) {
                        fakeInput.value = '[CONFIRM_GMAIL_SEND]';
                        document.getElementById('submitBtn').click();
                    }
                });
                cancelBtn.addEventListener('click', () => {
                    confirmBtn.disabled = true;
                    cancelBtn.disabled = true;
                    cancelBtn.innerHTML = '<i class="fa-solid fa-check"></i> Cancelled';
                    cancelBtn.style.background = '#dc2626';
                    const fakeInput = document.getElementById('userInput');
                    if (fakeInput) {
                        fakeInput.value = '[CANCEL_GMAIL_SEND]';
                        document.getElementById('submitBtn').click();
                    }
                });
                wrapper.appendChild(gmailCard);
            }
        }
        
        if (msgIndex !== null) {
            createActionButtons(wrapper, msgIndex, feedbackVal, true, text);
        }

        // Re-render PDF download card if this message generated a PDF
        if (msgObj && msgObj.pdf_url) {
            const pdfUrl  = window.location.origin + msgObj.pdf_url;
            const pdfName = msgObj.pdf_name || 'PepperReport.pdf';
            const dlCard  = document.createElement('div');
            dlCard.className = 'pdf-download-card';
            dlCard.innerHTML = `
                <div class="pdf-card-icon"><i class="fa-solid fa-file-pdf"></i></div>
                <div class="pdf-card-text">
                    <span class="pdf-card-title">PDF Report Available</span>
                    <span class="pdf-card-sub">${pdfName}</span>
                </div>
                <button class="pdf-dl-btn">
                    <i class="fa-solid fa-download"></i> Download PDF
                </button>
            `;
            const btn = dlCard.querySelector('.pdf-dl-btn');
            btn.addEventListener('click', () => {
                // Simplest possible download: let browser handle it natively
                window.open(pdfUrl, '_blank');
                btn.innerHTML = '<i class="fa-solid fa-check"></i> Downloaded!';
                btn.style.background = '#16a34a';
            });
            wrapper.appendChild(dlCard);
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

    if (!isResume && !currentUserId && isAgentMode) {
        showGuestLoginPrompt(true);
        return;
    }

    if (!isResume && !currentUserId && !isAgentMode && getGuestQuestionCount() >= GUEST_QUESTION_LIMIT) {
        showGuestLoginPrompt(true);
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
    let frontendThinkAccum = '';
    let frontendAnswerAccum = '';

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
                const upRes = await fetch('/api/upload_files', { method: 'POST', body: formData });
                const upData = await upRes.json();
                if (upRes.ok && upData.status === 'success') {
                    finalAttachments = upData.files;
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

        let newMsg = { role: 'user', content: text };
        if (finalAttachments.length > 0) {
            newMsg.attachments = finalAttachments;
        }

        chatMessages.push(newMsg);
        appendMessage(text, 'user', newMsg, chatMessages.length - 1);
        userInput.value = '';
        resizeComposer();

        if (!currentUserId && !isAgentMode) {
            const guestCount = incrementGuestQuestionCount();
            if (guestCount >= GUEST_QUESTION_LIMIT) {
                showGuestLoginPrompt(true);
            } else {
                syncGuestAccessState();
            }
            updateGuestInputUi();
        }
        
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
    const effectiveThinkMode = isAgentMode ? true : isThinkMode;
    const effectiveWebMode = isAgentMode ? false : isWebMode;
    submitBtn.className = 'submit-btn ' + ((effectiveThinkMode || effectiveWebMode && !frontendAnswerAccum) ? 'thinking-state' : 'answering-state');
    submitBtn.innerHTML = '<i class="fa-solid fa-pause"></i>';

    const assistantContainer = document.createElement('div');
    assistantContainer.className = 'message-bubble assistant';
    assistantWrapper.appendChild(assistantContainer);

    if (effectiveThinkMode || effectiveWebMode) {
        thinkWrapper = document.createElement('div');
        thinkWrapper.className = 'think-wrapper';
        if (!isResume) thinkWrapper.classList.add('collapsed');
        
        thinkHeader = document.createElement('div');
        thinkHeader.className = 'think-header';
        
        let initialLabel = (isResume && frontendAnswerAccum) ? 'Thought Process' : (effectiveThinkMode ? 'Thinking...' : 'Searching the web...');
        let initialIcon = (isResume && frontendAnswerAccum) ? '<i class="fa-solid fa-circle-check"></i>' : (effectiveThinkMode ? '<i class="fa-solid fa-atom fa-spin"></i>' : '<i class="fa-solid fa-globe fa-spin"></i>');
        
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

    // === Fetch & Stream ===
    let hasStartedTimer = false;
    let forcedEndThinking = false;
    const attachmentsPayload = isResume ? [] : (chatMessages[chatMessages.length - 1]?.attachments || []);
    console.log('DEBUG SEND payload:', attachmentsPayload, isAgentMode);

    try {
        const response = await fetch('/api/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                chat_id: currentChatId,
                user_id: currentUserId,
                message: isResume ? '' : chatMessages[chatMessages.length - 1]?.content || '',
                messages: chatMessages,
                attachments: attachmentsPayload,
                think_mode: isAgentMode ? true : isThinkMode,
                web_mode: isAgentMode ? false : isWebMode,
                is_resume: isResume,
                agent_mode: isAgentMode,
                user_timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || '',
            }),
            signal: currentAbortController.signal,
        });

        const reader = response.body.getReader();
        const decoder = new TextDecoder();

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            const chunk = decoder.decode(value, { stream: true });
            const lines = chunk.split('\n');

            for (const line of lines) {
                if (!line.startsWith('data: ')) continue;
                const dataStr = line.slice(6);

                if (dataStr === '[DONE]') {
                    isGenerating = false;
                    currentAbortController = null;
                    submitBtn.className = 'submit-btn';
                    submitBtn.innerHTML = '<i class="fa-solid fa-arrow-up"></i>';
                    if (thinkTimerInterval) clearInterval(thinkTimerInterval);

                    // Reset any pending confirm buttons that might be stuck spinning
                    document.querySelectorAll('.gmail-confirm-btn').forEach(btn => {
                        if (btn.innerHTML.includes('fa-spinner') || btn.innerHTML.includes('Sending...')) {
                            btn.innerHTML = '<i class="fa-solid fa-check"></i> Sent';
                            btn.style.background = '#16a34a';
                        }
                    });

                    // Finalize think header
                    if (thinkWrapper) {
                        if (!thinkStartTime) thinkStartTime = Date.now();
                        const elapsed = ((Date.now() - thinkStartTime) / 1000).toFixed(0);
                        thinkHeader.querySelector('.think-icon').innerHTML = '<i class="fa-solid fa-circle-check"></i>';
                        
                        if (effectiveThinkMode && !effectiveWebMode && frontendThinkAccum.trim().length === 0) {
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
                    
                    const msgStore = { role: 'assistant', content: rawAccumText, sources: currentSources };
                    if (isResume && resumeIndex !== null) {
                        chatMessages[resumeIndex] = msgStore;
                        createActionButtons(assistantWrapper, resumeIndex, 0, true, rawAccumText);
                    } else {
                        chatMessages.push(msgStore);
                        createActionButtons(assistantWrapper, chatMessages.length - 1, 0, true, rawAccumText);
                    }

                    // Apply syntax highlighting + math rendering after streaming completes
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

                        // Header
                        const headerDiv = document.createElement('div');
                        headerDiv.className = 'sources-header';
                        headerDiv.innerHTML = `<i class="fa-solid fa-magnifying-glass"></i> Read ${srcs.length} sources`;
                        thinkSourcesBar.appendChild(headerDiv);

                        // Scrollable cards container
                        const scrollContainer = document.createElement('div');
                        scrollContainer.className = 'sources-scroll';

                        srcs.forEach(s => {
                            const card = document.createElement('a');
                            card.className = 'source-card';
                            card.href = s.url;
                            card.target = '_blank';
                            card.rel = 'noopener noreferrer';

                            let domain = '';
                            try { domain = new URL(s.url).hostname.replace('www.',''); } catch {}
                            const favUrl = getFavicon(s.url);

                            card.innerHTML = `
                                ${favUrl ? `<img class="source-card-favicon" src="${favUrl}" alt="">` : ''}
                                <div class="source-card-text">
                                    <span class="source-card-title">${s.title || domain}</span>
                                    <span class="source-card-domain">${domain}</span>
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
                        let textChunk = data.text;
                        rawAccumText += textChunk;
                        
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
                                thinkContent.innerHTML = renderMd(frontendThinkAccum);
                                if (frontendThinkAccum.trim().length > 0 && thinkWrapper.style.display === 'none') {
                                    thinkWrapper.style.display = 'block';
                                }
                            }
                            if (contentBox) {
                                contentBox.innerHTML = renderMd(frontendAnswerAccum);
                            }
                            
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
                            if (contentBox) {
                                contentBox.innerHTML = renderMd(frontendAnswerAccum);
                            }
                        }
                        scrollToBottom();
                    }

                    // === PDF READY — show download card ===
                    if (data.pdf_ready && data.pdf_url) {
                        const pdfUrl  = window.location.origin + data.pdf_url;
                        const pdfName = data.pdf_name || 'PepperReport.pdf';

                        const dlCard = document.createElement('div');
                        dlCard.className = 'pdf-download-card';
                        dlCard.innerHTML = `
                            <div class="pdf-card-icon"><i class="fa-solid fa-file-pdf"></i></div>
                            <div class="pdf-card-text">
                                <span class="pdf-card-title">Your PDF Report is Ready!</span>
                                <span class="pdf-card-sub">${pdfName}</span>
                            </div>
                            <button class="pdf-dl-btn" id="dlBtn_${Date.now()}">
                                <i class="fa-solid fa-download"></i> Download PDF
                            </button>
                        `;

                        // Use fetch→Blob to force save-as dialog (avoids "file wasn't available" browser error)
                        const btn = dlCard.querySelector('.pdf-dl-btn');
                        btn.addEventListener('click', () => {
                            // Simplest possible download: let browser handle it natively
                            window.open(pdfUrl, '_blank');
                            btn.innerHTML = '<i class="fa-solid fa-check"></i> Downloaded!';
                            btn.style.background = '#16a34a';
                        });

                        assistantWrapper.appendChild(dlCard);
                        scrollToBottom();
                        continue;
                    }

                    // === GMAIL CONFIRM — detect marker in text and render buttons ===
                    if (data.text && data.text.includes('[GMAIL_CONFIRM_PENDING]')) {
                        // Strip the marker from displayed text
                        frontendAnswerAccum = frontendAnswerAccum.replace('[GMAIL_CONFIRM_PENDING]', '');
                        rawAccumText = rawAccumText.replace('[GMAIL_CONFIRM_PENDING]', '');
                        if (contentBox) {
                            contentBox.innerHTML = renderMd(frontendAnswerAccum);
                        }

                        // Create Gmail confirmation card
                        const gmailCard = document.createElement('div');
                        gmailCard.className = 'gmail-confirm-card';
                        gmailCard.style = "margin-top: 12px; padding-top: 12px; border-top: 1px solid var(--outline-variant);";
                        gmailCard.innerHTML = `
                            <div class="gmail-card-actions" style="display: flex; gap: 10px; align-items: center; margin-bottom: 8px;">
                                <button class="gmail-confirm-btn" id="gmailConfirm_${Date.now()}" style="padding: 8px 16px; border-radius: 8px; font-weight: 500; font-size: 0.9em; display: inline-flex; align-items: center; gap: 8px; cursor: pointer; transition: all 0.2s ease; border: none; background: #6366f1; color: white; min-width: 130px; justify-content: center;">
                                    <i class="fa-solid fa-paper-plane"></i> Confirm Send
                                </button>
                                <button class="gmail-cancel-btn" id="gmailCancel_${Date.now()}" style="padding: 8px 16px; border-radius: 8px; font-weight: 500; font-size: 0.9em; display: inline-flex; align-items: center; gap: 8px; cursor: pointer; transition: all 0.2s ease; border: 1px solid var(--outline-variant); background: transparent; color: var(--text-color); min-width: 100px; justify-content: center;">
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
                            confirmBtn.style.background = '#6366f1';
                            // Send confirm message to backend
                            const fakeInput = document.getElementById('userInput');
                            if (fakeInput) {
                                fakeInput.value = '[CONFIRM_GMAIL_SEND]';
                                document.getElementById('submitBtn').click();
                            }
                        });
                        cancelBtn.addEventListener('click', () => {
                            confirmBtn.disabled = true;
                            cancelBtn.disabled = true;
                            cancelBtn.innerHTML = '<i class="fa-solid fa-check"></i> Cancelled';
                            cancelBtn.style.background = '#dc2626';
                            const fakeInput = document.getElementById('userInput');
                            if (fakeInput) {
                                fakeInput.value = '[CANCEL_GMAIL_SEND]';
                                document.getElementById('submitBtn').click();
                            }
                        });

                        assistantWrapper.appendChild(gmailCard);
                        scrollToBottom();
                        
                        scrollToBottom();
                    }

                } catch (e) { /* partial JSON */ }
            }
        }
    } catch (e) {
        if (e.name === 'AbortError') {
            if (thinkTimerInterval) clearInterval(thinkTimerInterval);
            if (thinkHeader && !forcedEndThinking) {
                const durationStr = thinkDurationEl ? thinkDurationEl.innerText : '';
                thinkHeader.querySelector('.think-icon').innerHTML = '<i class="fa-solid fa-pause"></i>';
                thinkHeader.querySelector('.think-label').innerText = 'Thinking Paused';
                if (durationStr) thinkDurationEl.innerText = durationStr;
            }

            const wrap = document.createElement('div');
            wrap.className = 'markdown-content';
            wrap.innerHTML = '<br><em style="color:var(--primary-dim); font-size: 0.9em;"><i class="fa-solid fa-pause"></i> Generation paused by user</em>';
            contentBox.appendChild(wrap);
            
            const msgStore = { role: 'assistant', content: rawAccumText, sources: currentSources };
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
const fileInput = document.getElementById('fileInput');
const uploadBtn = document.getElementById('uploadBtn');
const attachmentsPreview = document.getElementById('attachmentsPreview');

uploadBtn.addEventListener('click', () => {
    if (!currentUserId && isAgentMode) {
        showGuestLoginPrompt(true);
        return;
    }
    fileInput.click();
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
    attachmentsPreview.innerHTML = '';
    pendingFiles.forEach((f, idx) => {
        const pill = document.createElement('div');
        pill.className = 'file-pill';
        
        let iconClass = 'fa-file-lines';
        if (f.type.startsWith('image/')) iconClass = 'fa-file-image';
        else if (f.type === 'application/pdf' || f.name.toLowerCase().endsWith('.pdf')) iconClass = 'fa-file-pdf';

        const sizeStr = (f.size / 1024 / 1024).toFixed(2) + ' MB';
        
        pill.innerHTML = `
            <i class="fa-solid ${iconClass}"></i>
            <span class="file-pill-name">${f.name}</span>
            <span class="file-pill-size">${sizeStr}</span>
            <button class="file-pill-remove" onclick="removePendingFile(${idx})">
                <i class="fa-solid fa-xmark"></i>
            </button>
        `;
        attachmentsPreview.appendChild(pill);
    });
}

function renderMessageAttachments(attachmentsArr) {
    // Legacy function kept for compatibility — now returns empty
    return '';
}

function createAttachmentCard(att) {
    const card = document.createElement('div');
    card.className = 'user-msg-attachment-card';
    
    let iconClass = 'fa-file-lines';
    let iconColor = 'var(--primary-dim)';
    const isPdf = att.content_type === 'application/pdf' || (att.original_name && att.original_name.toLowerCase().endsWith('.pdf'));
    if (att.content_type && att.content_type.startsWith('image/')) {
        iconClass = 'fa-file-image';
        iconColor = '#4c8de2';
    } else if (isPdf) {
        iconClass = 'fa-file-pdf';
        iconColor = '#e2574c';
    }
    
    const sizeStr = att.size ? (att.size / 1024 / 1024).toFixed(2) + ' MB' : '';
    
    card.innerHTML = `
        <div class="att-icon" style="color:${iconColor}">
            <i class="fa-solid ${iconClass}"></i>
        </div>
        <span class="att-name">${att.original_name || 'file'}</span>
        ${sizeStr ? `<span class="att-size">(${sizeStr})</span>` : ''}
    `;
    
    // Construct the PDF URL: prefer att.url, fallback to building from saved_path or file_id
    let pdfUrl = att.url || null;
    if (!pdfUrl && att.saved_path) {
        // Extract filename from saved_path (e.g. "uuid.pdf" from GridFS key)
        const parts = att.saved_path.replace(/\\/g, '/').split('/');
        const fname = parts[parts.length - 1];
        pdfUrl = '/uploads/' + fname;
    }
    if (!pdfUrl && att.file_id) {
        const ext = att.original_name ? att.original_name.substring(att.original_name.lastIndexOf('.')) : '.pdf';
        pdfUrl = '/uploads/' + att.file_id + ext;
    }
    
    if (isPdf && pdfUrl) {
        card.style.cursor = 'pointer';
        card.addEventListener('click', () => openPdfPreview(pdfUrl, att.original_name));
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
}

function closePdfPreview() {
    const overlay = document.getElementById('pdfPreviewOverlay');
    const frame = document.getElementById('pdfPreviewFrame');
    
    if (overlay) overlay.classList.remove('show');
    if (frame) frame.src = '';
    document.body.style.overflow = '';
}

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
userInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
        e.preventDefault();
        handleSend(false, null);
    }
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
    searchHistoryModal.classList.add('show');
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
        const fetchUrl = `/api/history?user_id=${encodeURIComponent(currentUserId)}`;
        const res = await fetch(fetchUrl);
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
    searchHistoryModal.classList.remove('show');
    clearTimeout(previewHoverTimer);
});

// Close modal when clicking outside
searchHistoryModal.addEventListener('click', (e) => {
    if (e.target === searchHistoryModal) {
        searchHistoryModal.classList.remove('show');
        clearTimeout(previewHoverTimer);
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
            if (entry.id === currentChatId) li.classList.add('active');

            const timeLabel = entry.updatedAt.toLocaleString([], {
                month: 'short',
                day: 'numeric',
                hour: '2-digit',
                minute: '2-digit'
            });

            li.innerHTML = `
                <div class="history-item-main">
                    <div class="history-item-text"></div>
                    <div class="history-item-meta">${entry.agentMode ? 'Agent' : 'Chat'} · ${timeLabel}</div>
                </div>
                <div class="history-item-actions">
                    <button class="modal-action-btn open-btn" title="Open"><i class="fa-solid fa-arrow-up-right-from-square"></i></button>
                    <button class="modal-action-btn edit-btn" title="Edit"><i class="fa-solid fa-pencil"></i></button>
                    <button class="modal-action-btn delete-btn" title="Delete"><i class="fa-regular fa-trash-can"></i></button>
                </div>
            `;
            
            const openBtn = li.querySelector('.open-btn');
            const editBtn = li.querySelector('.edit-btn');
            const deleteBtn = li.querySelector('.delete-btn');
            const textDiv = li.querySelector('.history-item-text');
            textDiv.innerText = entry.title;
            textDiv.title = entry.title;
            
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
            
            // Double click title to gently open chat
            textDiv.addEventListener('dblclick', () => {
                loadChat(entry.id);
                searchHistoryModal.classList.remove('show');
            });
            
            // Actions
            openBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                loadChat(entry.id);
                searchHistoryModal.classList.remove('show');
            });
            
            editBtn.addEventListener('click', async (e) => {
                e.stopPropagation();
                const newTitle = prompt("Rename chat:", entry.title);
                if (newTitle && newTitle.trim() && newTitle.trim() !== entry.title) {
                    try {
                        const rr = await fetch(`/api/history/${entry.id}`, {
                            method: 'PUT',
                            headers: { 'Content-Type': 'application/json' },
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
                if (confirm("Delete this chat?")) {
                    try {
                        const dr = await fetch(`/api/history/${entry.id}`, { method: 'DELETE' });
                        if (dr.ok) {
                            li.remove();
                            if (currentChatId === entry.id) {
                                document.getElementById('newChatBtn').click();
                            }
                            loadHistory(); // refresh global sidebar
                            previewContent.classList.add('hidden');
                            previewEmptyState.classList.remove('hidden');
                        }
                    } catch(err) { console.error("Delete failed", err); }
                }
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
        const res = await fetch(`/api/history/${chatId}?user_id=${encodeURIComponent(currentUserId)}`);
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
                roleDiv.innerText = m.role === 'assistant' ? 'MOF' : 'You';

                const contentDiv = document.createElement('div');
                contentDiv.className = 'preview-msg-body';
                
                if (m.role === 'assistant') {
                    // Strip huge <think> blocks for preview clarity
                    let content = m.content;
                    content = content.replace(/<think>[\s\S]*?<\/think>/g, '<div class="preview-think-note">Thought process completed</div>');
                    contentDiv.innerHTML = marked.parse(content);
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

// ============ Guest Navigation: Settings Dropdown, Theme & Language ============
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

    // ── Login / Register buttons → redirect to dedicated pages ──
    const guestLoginBtn = document.getElementById('guestLoginBtn');
    const guestRegisterBtn = document.getElementById('guestRegisterBtn');

    if (guestLoginBtn) {
        guestLoginBtn.addEventListener('click', () => {
            window.location.href = '/static/login.html';
        });
    }

    if (guestRegisterBtn) {
        guestRegisterBtn.addEventListener('click', () => {
            window.location.href = '/static/register.html';
        });
    }

    // ── Theme Toggle ──
    const savedTheme = localStorage.getItem('pepperTheme') || 'dark';
    applyTheme(savedTheme);

    nav.querySelectorAll('.theme-btn').forEach(btn => {
        if (btn.dataset.theme === savedTheme) btn.classList.add('active');
        else btn.classList.remove('active');

        btn.addEventListener('click', () => {
            applyTheme(btn.dataset.theme);
            localStorage.setItem('pepperTheme', btn.dataset.theme);
            syncPreferenceControls();
            saveUserPreferences({ theme: btn.dataset.theme });
        });
    });

    function applyTheme(theme) {
        if (theme === 'dark') {
            document.documentElement.setAttribute('data-theme', 'dark');
            
        } else {
            document.documentElement.removeAttribute('data-theme');
            
        }
    }
    window.applyPepperTheme = applyTheme;

    // ── Language Selector ──
    const savedLang = localStorage.getItem('pepperLang') || 'en';
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
        const translations = {
            en: {
                greeting: 'How can I help you today?',
                placeholder: 'What do you want to know?',
                thinkMode: 'Think Mode',
                searchMode: 'Search Mode',
                login: 'Login',
                loginBtn: 'Login',
                registerBtn: 'Register',
                agentGreeting: 'Your personalize AI agent',
                settingsLabel: 'Language',
                navChat: 'Chat',
                navSearch: 'Search',
                navAgent: 'Agent',
                navHistory: 'History',
                loadingHistory: 'Loading history...',
                guestLimitText: 'Get smarter responses, upload files and images, and unlock more features.',
                guestLimitRegisterBtn: 'Sign up for free',
                agentRequiresLogin: 'Login required for Agent mode',
                ctxSettings: 'Settings',
                ctxTheme: 'Theme',
                ctxLang: 'Language',
                ctxFiles: 'Files',
                ctxAccount: 'Account',
                ctxLogout: 'Logout'
            },
            zh: {
                greeting: '今天我能为您做些什么？',
                placeholder: '您想了解什么？',
                thinkMode: '思考模式',
                searchMode: '联网搜索',
                login: '登录',
                loginBtn: '登录',
                registerBtn: '注册',
                agentGreeting: '您的专属 AI 智能助手',
                settingsLabel: '语言',
                navChat: '对话',
                navSearch: '搜索',
                navAgent: '深度分析',
                navHistory: '历史记录',
                loadingHistory: '加载历史中...',
                guestLimitText: '获取更加智能的回复、上传文件和图片，并获享更多功能。',
                guestLimitRegisterBtn: '免费注册',
                agentRequiresLogin: 'Agent 模式需要先登录',
                ctxSettings: '设置',
                ctxTheme: '主题',
                ctxLang: '语言',
                ctxFiles: '文件',
                ctxAccount: '账户',
                ctxLogout: '退出登录'
            },
            ms: {
                greeting: 'Apa yang boleh saya bantu hari ini?',
                placeholder: 'Apa yang anda ingin tahu?',
                thinkMode: 'Mod Pemikiran',
                searchMode: 'Mod Carian',
                login: 'Log Masuk',
                loginBtn: 'Log Masuk',
                registerBtn: 'Daftar',
                agentGreeting: 'Ejen AI peribadi anda',
                settingsLabel: 'Bahasa',
                navChat: 'Sembang',
                navSearch: 'Carian',
                navAgent: 'Ejen',
                navHistory: 'Sejarah',
                loadingHistory: 'Memuatkan sejarah...',
                guestLimitText: 'Dapatkan jawapan yang lebih pintar, muat naik fail dan imej, serta buka lebih banyak fungsi.',
                guestLimitRegisterBtn: 'Daftar percuma',
                agentRequiresLogin: 'Log masuk diperlukan untuk mod Agent',
                ctxSettings: 'Tetapan',
                ctxTheme: 'Tema',
                ctxLang: 'Bahasa',
                ctxFiles: 'Fail',
                ctxAccount: 'Akaun',
                ctxLogout: 'Log Keluar'
            }
        };

        const t = translations[lang] || translations.en;
        const inp = document.getElementById('userInput');
        if (inp) inp.placeholder = t.placeholder;
        const thinkSpan = document.querySelector('#thinkToggle span');
        if (thinkSpan) thinkSpan.textContent = t.thinkMode;
        const webSpan = document.querySelector('#webToggle span');
        if (webSpan) webSpan.textContent = t.searchMode;
        if (guestLoginBtn) guestLoginBtn.textContent = t.loginBtn;
        if (guestRegisterBtn) guestRegisterBtn.textContent = t.registerBtn;
        
        // Update sidebar nav items
        const chatTxt = document.querySelector('#newChatBtn .nav-text');
        if (chatTxt) chatTxt.textContent = t.navChat;
        const searchTxt = document.querySelector('#openSearchModalBtn .nav-text');
        if (searchTxt) searchTxt.textContent = t.navSearch;
        const agentTxt = document.querySelector('#agentModeBtn .nav-text');
        if (agentTxt) agentTxt.textContent = t.navAgent;
        const historyTxt = document.querySelector('#historyToggleBtn .nav-text');
        if (historyTxt) historyTxt.textContent = t.navHistory;
        const loadHist = document.querySelector('.history-placeholder .nav-text');
        if (loadHist) loadHist.textContent = t.loadingHistory;

        // Update settings label
        const settingsLabel = nav.querySelector('.settings-label');
        if (settingsLabel) settingsLabel.textContent = t.settingsLabel;

        // Update User Context Menu
        const ctxSettingsTxt = document.querySelector('#ctxSettingsMenuBtn span');
        if (ctxSettingsTxt) ctxSettingsTxt.textContent = t.ctxSettings;
        const ctxFilesTxt = document.querySelector('#ctxFilesBtn span');
        if (ctxFilesTxt) ctxFilesTxt.textContent = t.ctxFiles;
        const ctxAccountTxt = document.querySelector('#ctxAccountBtn span');
        if (ctxAccountTxt) ctxAccountTxt.textContent = t.ctxAccount;
        const ctxLogoutTxt = document.querySelector('#ctxLogoutBtn span');
        if (ctxLogoutTxt) ctxLogoutTxt.textContent = t.ctxLogout;
        
        const ctxHeaders = document.querySelectorAll('.ctx-settings-header');
        if (ctxHeaders.length >= 2) {
            ctxHeaders[0].textContent = t.ctxTheme;
            ctxHeaders[1].textContent = t.ctxLang;
        }

        if (!currentUserId) {
            const ud = document.getElementById('userDisplay');
            if (ud) ud.innerText = t.login;
        }
        window._pepperLang = t;
        window.applyPepperLang = applyLang;
        if (logoContainer && isFirstMessage && !isAgentMode) {
            logoContainer.innerHTML = getNormalLandingMarkup();
        }
        updateGuestLimitBannerCopy();
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
    const settingsIcon = document.getElementById('ctxSettingsIcon');
    const currentUserId = localStorage.getItem('pepperUserId');
    
    function hashToHSL(str) {
        let hash = 0;
        for (let i = 0; i < str.length; i++) {
            hash = str.charCodeAt(i) + ((hash << 5) - hash);
        }
        const h = Math.abs(hash % 360);
        return `hsl(${h}, 70%, 60%)`;
    }

    function hydrateUser() {
        const jwt = localStorage.getItem('pepperJwt');
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
                avatarDisplay.innerHTML = firstLetter;
                avatarDisplay.style.background = `linear-gradient(135deg, ${bgColor}, #333333)`;
            }
        } else if (profileName) {
            if (userDisplayName) userDisplayName.innerText = profileName;
            if (userEmailDisplay) userEmailDisplay.innerText = '';
            
            if (avatarDisplay) {
                const firstLetter = profileName.charAt(0).toUpperCase();
                const bgColor = hashToHSL(profileName);
                avatarDisplay.innerHTML = firstLetter;
                avatarDisplay.style.background = `linear-gradient(135deg, ${bgColor}, #333333)`;
            }
        }
    }
    hydrateUser();

    if(showLoginBtn && contextMenu) {
        showLoginBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            if (!localStorage.getItem('pepperUserId')) {
                window.location.href = '/static/login.html';
            } else {
                const isShowing = contextMenu.classList.contains('show');
                contextMenu.classList.toggle('show', !isShowing);
            }
        });

        document.addEventListener('click', (e) => {
            if (!contextMenu.contains(e.target) && !showLoginBtn.contains(e.target)) {
                contextMenu.classList.remove('show');
                if(settingsBlock) settingsBlock.classList.remove('show');
                if(settingsIcon) settingsIcon.className = 'fa-solid fa-chevron-down';
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
        logoutBtn.addEventListener('click', () => {
            localStorage.removeItem('pepperJwt');
            localStorage.removeItem('pepperUserId');
            localStorage.removeItem('pepperUsername');
            localStorage.removeItem('pepperDisplayName');
            localStorage.removeItem('pepperAvatar');
            localStorage.removeItem('pepperGuestQuestionCount');
            window.location.href = '/static/login.html';
        });
    }

    if (accountBtn) {
        accountBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            contextMenu.classList.remove('show');
            window.dispatchEvent(new CustomEvent('open-mof-account-page'));
        });
    }

    if(settingsBtn && settingsBlock) {
        settingsBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            const isOpen = settingsBlock.classList.contains('show');
            settingsBlock.classList.toggle('show', !isOpen);
            if(settingsIcon) settingsIcon.className = isOpen ? 'fa-solid fa-chevron-down' : 'fa-solid fa-chevron-right';
        });
    }
    
    // Bind context settings toggles
    const ctxThemeGroup = document.getElementById('ctxThemeToggleGroup');
    if (ctxThemeGroup) {
        ctxThemeGroup.querySelectorAll('.theme-btn').forEach(btn => {
            const savedTheme = localStorage.getItem('pepperTheme') || 'dark';
            btn.classList.toggle('active', btn.dataset.theme === savedTheme);
            btn.addEventListener('click', () => {
                localStorage.setItem('pepperTheme', btn.dataset.theme);
                if (window.applyPepperTheme) window.applyPepperTheme(btn.dataset.theme);
                syncPreferenceControls();
                saveUserPreferences({ theme: btn.dataset.theme });
            });
        });
    }
    
    const ctxLangSel = document.getElementById('ctxLangSelector');
    if (ctxLangSel) {
        ctxLangSel.querySelectorAll('.lang-btn').forEach(btn => {
            const currentLang = localStorage.getItem('pepperLang') || 'en';
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
    const themeBtn = document.getElementById('accountThemeToggle');
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

    const languageLabels = { en: 'EN', zh: '中文', ms: 'BM' };
    const languageOrder = ['en', 'zh', 'ms'];
    const accountCopy = {
        zh: {
            welcomePrefix: '欢迎，',
            sentenceEnd: '。',
            manageAccount: '管理您的mof账户。',
            navAccount: '账户',
            navSecurity: '安全',
            navData: '数据',
            backHome: '返回主页面',
            profileTitle: '您的账户',
            profileSubtitle: '管理您的账户信息。',
            labelName: '全名',
            labelEmail: '邮箱',
            labelSubscription: '订阅',
            subscriptionText: '管理您的订阅',
            manageSubBtn: '管理 ↗',
            labelCreated: '账户创建',
            editNameBtn: '编辑姓名',
            updateEmailBtn: '更新邮箱',
            loginMethodsTitle: '登录方法',
            loginMethodsSubtitle: '管理您登录 MOF 的方式。',
            loginEmail: '邮箱和密码',
            loginEmailSub: '启用邮箱登录',
            loginAppleSub: '连接您的 Apple 账户',
            btnEnabled: '启用',
            btnDisabled: '停用',
            btnConnect: '连接',
            securitySubtitle: '管理您的账户安全设置。',
            dataTitle: '您的数据',
            dataSubtitle: '管理您存储在 MOF 的个人数据。',
            cookieTitle: 'Cookie 设置',
            cookieDesc: '管理您的分析和广告 Cookie 偏好设置。',
            downloadTitle: '下载账户数据',
            downloadDesc: '您可以在下方下载与您的账户关联的所有数据。此数据包括存储在所有 MOF 产品中的一切。',
            deleteTitle: '删除账户',
            deleteDesc: '删除您的账户以及 MOF 平台上的关联数据。如果您在 30 天内再次登录，可以恢复您的账户。',
            manageBtn: '管理',
            downloadBtn: '下载',
            deleteBtn: '删除',
            downloadSentTitle: '邮件已发送!',
            downloadSentDesc: '我们将很快向您发送一封包含数据下载链接的邮件。',
            closeBtn: '关闭',
            deleteDialogTitle: '您确定吗?',
            deleteDialogDesc: '此操作将删除您所有与 MOF 关联的数据，并将您退出登录。如果您在 30 天内再次登录，可以恢复您的数据。30 天后您的数据将被永久删除。',
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
            securityMfaTitle: '多因素身份验证',
            securityMfaDesc: '使用第二个身份验证因素保护您的账户。',
            securityEnableMfaBtn: '启用 MFA',
            securityRecoveryTitle: '恢复代码',
            securityRecoveryDesc: '您需要至少启用一种多因素方法才能生成恢复代码。',
            securityNewPasswordLabel: '新密码',
            securitySavePasswordBtn: '保存密码',
            securitySetPasswordSubtitle: '填写下方表单以更改您的登录密码。',
            passwordTooShort: '密码至少需要8个字符',
            googleUnlinkConfirm: '确定要解除 Google 账户绑定吗？',
            googleUnlinkTitle: '您可能会被退出登录',
            googleUnlinkDesc: '取消关联此方法可能会将您退出账户登录。',
            googleNotLinked: '请先在账户设置中绑定您的 Google 账号后再使用连接器。'
        },
        en: {
            welcomePrefix: 'Welcome, ',
            sentenceEnd: '.',
            manageAccount: 'Manage your mof account.',
            navAccount: 'Account',
            navSecurity: 'Security',
            navData: 'Data',
            backHome: 'Back to home',
            profileTitle: 'Your account',
            profileSubtitle: 'Manage your account information.',
            labelName: 'Full name',
            labelEmail: 'Email',
            labelSubscription: 'Subscription',
            subscriptionText: 'Manage your subscription',
            manageSubBtn: 'Manage ↗',
            labelCreated: 'Account created',
            editNameBtn: 'Edit name',
            updateEmailBtn: 'Update email',
            loginMethodsTitle: 'Login methods',
            loginMethodsSubtitle: 'Manage how you log in to MOF.',
            loginEmail: 'Email and password',
            loginEmailSub: 'Enable email login',
            loginAppleSub: 'Connect your Apple account',
            btnEnabled: 'Enabled',
            btnDisabled: 'Disabled',
            btnConnect: 'Connect',
            securitySubtitle: 'Manage your account security settings.',
            dataTitle: 'Your data',
            dataSubtitle: 'Manage the personal data you store with MOF.',
            cookieTitle: 'Cookie settings',
            cookieDesc: 'Manage your analytics and advertising cookie preferences.',
            downloadTitle: 'Download account data',
            downloadDesc: 'You can download all data associated with your account below. This includes everything stored across MOF products.',
            deleteTitle: 'Delete account',
            deleteDesc: 'Delete your account and associated MOF platform data. If you log in again within 30 days, your account can be restored.',
            manageBtn: 'Manage',
            downloadBtn: 'Download',
            deleteBtn: 'Delete',
            downloadSentTitle: 'Email sent!',
            downloadSentDesc: 'We will soon send you an email containing a data download link.',
            closeBtn: 'Close',
            deleteDialogTitle: 'Are you sure?',
            deleteDialogDesc: 'This will delete all data associated with MOF and log you out. If you log in again within 30 days, your data can be restored. After 30 days your data will be permanently deleted.',
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
            securityMfaTitle: 'Multi-factor authentication',
            securityMfaDesc: 'Protect your account with a second authentication factor.',
            securityEnableMfaBtn: 'Enable MFA',
            securityRecoveryTitle: 'Recovery codes',
            securityRecoveryDesc: 'You need to enable at least one multi-factor method to generate recovery codes.',
            securityNewPasswordLabel: 'New password',
            securitySavePasswordBtn: 'Save password',
            securitySetPasswordSubtitle: 'Fill in the form below to change your login password.',
            passwordTooShort: 'Password must be at least 8 characters',
            googleUnlinkConfirm: 'Are you sure you want to unlink your Google account?',
            googleUnlinkTitle: 'You may be signed out',
            googleUnlinkDesc: 'Removing this method may sign you out of your account.',
            googleNotLinked: 'Please link your Google account in Account Settings before using connectors.'
        },
        ms: {
            welcomePrefix: 'Selamat datang, ',
            sentenceEnd: '.',
            manageAccount: 'Urus akaun mof anda.',
            navAccount: 'Akaun',
            navSecurity: 'Keselamatan',
            navData: 'Data',
            backHome: 'Kembali ke halaman utama',
            profileTitle: 'Akaun anda',
            profileSubtitle: 'Urus maklumat akaun anda.',
            labelName: 'Nama penuh',
            labelEmail: 'E-mel',
            labelSubscription: 'Langganan',
            subscriptionText: 'Urus langganan anda',
            manageSubBtn: 'Urus ↗',
            labelCreated: 'Akaun dibuat',
            editNameBtn: 'Edit nama',
            updateEmailBtn: 'Kemaskini e-mel',
            loginMethodsTitle: 'Kaedah log masuk',
            loginMethodsSubtitle: 'Urus cara anda log masuk ke MOF.',
            loginEmail: 'E-mel dan kata laluan',
            loginEmailSub: 'Aktifkan log masuk e-mel',
            loginAppleSub: 'Sambung akaun Apple anda',
            btnEnabled: 'Aktif',
            btnDisabled: 'Tidak aktif',
            btnConnect: 'Sambung',
            securitySubtitle: 'Urus tetapan keselamatan akaun anda.',
            dataTitle: 'Data anda',
            dataSubtitle: 'Urus data peribadi yang anda simpan di MOF.',
            cookieTitle: 'Tetapan Cookie',
            cookieDesc: 'Urus pilihan cookie analitik dan pengiklanan anda.',
            downloadTitle: 'Muat turun data akaun',
            downloadDesc: 'Anda boleh memuat turun semua data yang berkaitan dengan akaun anda. Data ini termasuk semua yang disimpan merentas produk MOF.',
            deleteTitle: 'Padam akaun',
            deleteDesc: 'Padam akaun anda dan data berkaitan pada platform MOF. Jika anda log masuk semula dalam 30 hari, akaun anda boleh dipulihkan.',
            manageBtn: 'Urus',
            downloadBtn: 'Muat turun',
            deleteBtn: 'Padam',
            downloadSentTitle: 'E-mel dihantar!',
            downloadSentDesc: 'Kami akan menghantar e-mel yang mengandungi pautan muat turun data tidak lama lagi.',
            closeBtn: 'Tutup',
            deleteDialogTitle: 'Anda pasti?',
            deleteDialogDesc: 'Tindakan ini akan memadam semua data yang berkaitan dengan MOF dan melog anda keluar. Jika anda log masuk semula dalam 30 hari, data anda boleh dipulihkan. Selepas 30 hari, data anda akan dipadam secara kekal.',
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
            securityMfaTitle: 'Pengesahan pelbagai faktor',
            securityMfaDesc: 'Lindungi akaun anda dengan faktor pengesahan kedua.',
            securityEnableMfaBtn: 'Aktifkan MFA',
            securityRecoveryTitle: 'Kod pemulihan',
            securityRecoveryDesc: 'Anda perlu mengaktifkan sekurang-kurangnya satu kaedah berbilang faktor untuk menjana kod pemulihan.',
            securityNewPasswordLabel: 'Kata laluan baharu',
            securitySavePasswordBtn: 'Simpan kata laluan',
            securitySetPasswordSubtitle: 'Isi borang di bawah untuk menukar kata laluan log masuk anda.',
            passwordTooShort: 'Kata laluan mestilah sekurang-kurangnya 8 aksara',
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
        target.innerHTML = firstLetter;
        target.style.background = `linear-gradient(135deg, ${bgColor}, #111111)`;
    }

    function syncAccountLanguageLabel() {
        const lang = localStorage.getItem('pepperLang') || 'en';
        if (langLabel) langLabel.textContent = languageLabels[lang] || 'EN';
    }

    function renderAccountLanguage() {
        const lang = localStorage.getItem('pepperLang') || 'en';
        const copy = accountCopy[lang] || accountCopy.en;
        document.querySelectorAll('[data-account-i18n]').forEach(el => {
            const key = el.dataset.accountI18n;
            if (copy[key]) el.textContent = copy[key];
        });
        updateDeletePrompt();
        syncAccountLanguageLabel();
    }

    function getAccountEmail() {
        return localStorage.getItem('pepperUsername') || '';
    }

    function updateDeletePrompt() {
        if (!deleteEmailPrompt) return;
        const lang = localStorage.getItem('pepperLang') || 'en';
        const copy = accountCopy[lang] || accountCopy.en;
        const email = getAccountEmail();
        deleteEmailPrompt.innerHTML = `${copy.deleteEmailPrefix}<strong>${email}</strong>${copy.deleteEmailSuffix}`;
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

    function syncAccountThemeIcon() {
        if (!themeBtn) return;
        const isDark = (localStorage.getItem('pepperTheme') || 'dark') === 'dark';
        themeBtn.innerHTML = isDark ? '<i class="fa-regular fa-moon"></i>' : '<i class="fa-regular fa-sun"></i>';
        themeBtn.setAttribute('aria-label', isDark ? 'Dark mode' : 'Light mode');
    }

    function updateLoginMethodButtons(hasPassword, googleLinked, authProvider) {
        const lang = localStorage.getItem('pepperLang') || 'en';
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
                const lang = localStorage.getItem('pepperLang') || 'en';
                profileCreatedEl.textContent = d.toLocaleDateString(lang === 'zh' ? 'zh-CN' : lang === 'ms' ? 'ms-MY' : 'en-US', { year: 'numeric', month: 'long', day: 'numeric' });
            } else {
                profileCreatedEl.textContent = '—';
            }
        }
        if (profileAvatarEl) renderAvatar(profileAvatarEl);

        const token = localStorage.getItem('pepperJwt');
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
        Object.values(sections).forEach(s => { if (s) s.hidden = true; });
        if (sections[sectionKey]) sections[sectionKey].hidden = false;

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
        syncAccountThemeIcon();
        switchAccountSection('profile');
        overlay.classList.add('show');
        overlay.setAttribute('aria-hidden', 'false');
        document.body.classList.add('account-page-open');
    }

    function closeAccountPage() {
        overlay.classList.remove('show');
        overlay.setAttribute('aria-hidden', 'true');
        document.body.classList.remove('account-page-open');
    }

    window.addEventListener('open-mof-account-page', openAccountPage);

    if (backBtn) backBtn.addEventListener('click', closeAccountPage);

    overlay.querySelectorAll('.account-nav-item[data-account-section]').forEach(btn => {
        btn.addEventListener('click', () => switchAccountSection(btn.dataset.accountSection));
    });

    if (langBtn) {
        langBtn.addEventListener('click', () => {
            const currentLang = localStorage.getItem('pepperLang') || 'en';
            const nextLang = languageOrder[(languageOrder.indexOf(currentLang) + 1) % languageOrder.length] || 'zh';
            localStorage.setItem('pepperLang', nextLang);
            if (window.applyPepperLang) window.applyPepperLang(nextLang);
            renderAccountLanguage();
            syncPreferenceControls();
            saveUserPreferences({ language: nextLang });
        });
    }

    if (themeBtn) {
        themeBtn.addEventListener('click', () => {
            const currentTheme = localStorage.getItem('pepperTheme') || 'dark';
            const nextTheme = currentTheme === 'dark' ? 'light' : 'dark';
            localStorage.setItem('pepperTheme', nextTheme);
            if (window.applyPepperTheme) window.applyPepperTheme(nextTheme);
            syncPreferenceControls();
            syncAccountThemeIcon();
            saveUserPreferences({ theme: nextTheme });
        });
    }

    if (downloadBtn && downloadDialog) {
        downloadBtn.addEventListener('click', async () => {
            const token = localStorage.getItem('pepperJwt');
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
            const lang = localStorage.getItem('pepperLang') || 'en';
            const copy = accountCopy[lang] || accountCopy.en;
            if (deleteEmailInput && deleteEmailInput.value.trim().toLowerCase() !== getAccountEmail().toLowerCase()) {
                alert(copy.deleteMismatch);
                return;
            }
            const token = localStorage.getItem('pepperJwt');
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
                ['pepperJwt', 'pepperUserId', 'pepperUsername', 'pepperDisplayName', 'pepperAvatar', 'pepperGuestQuestionCount'].forEach(key => localStorage.removeItem(key));
                window.location.href = '/static/login.html';
            } catch (err) {
                syncDeleteConfirmState();
                alert(err.message || 'Delete failed');
            }
        });
    }

    window.addEventListener('mof-preferences-changed', () => {
        renderAccountLanguage();
        syncAccountThemeIcon();
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
            const token = localStorage.getItem('pepperJwt');
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
            const lang = localStorage.getItem('pepperLang') || 'en';
            const copy = accountCopy[lang] || accountCopy.en;
            const newEmail = editEmailInput ? editEmailInput.value.trim() : '';
            if (!newEmail || !newEmail.includes('@')) {
                if (editEmailError) editEmailError.textContent = copy.updateEmailLabel || '请输入有效的邮箱地址';
                return;
            }
            const token = localStorage.getItem('pepperJwt');
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
            const token = localStorage.getItem('pepperJwt');
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
            const lang = localStorage.getItem('pepperLang') || 'en';
            const copy = accountCopy[lang] || accountCopy.en;
            const pw = newPasswordInput.value;
            if (!pw || pw.length < 8) {
                if (newPasswordError) newPasswordError.textContent = copy.passwordTooShort;
                return;
            }
            const token = localStorage.getItem('pepperJwt');
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
    let googleLinkTokenClient = null;

    async function initGoogleLinkClient(callback) {
        await publicConfigReady;
        if (typeof google === 'undefined') { callback && callback(null, 'Google client not available'); return; }
        googleLinkTokenClient = google.accounts.oauth2.initTokenClient({
            client_id: googleOAuthClientId,
            scope: 'email profile',
            callback: async (tokenResponse) => {
                if (tokenResponse.error) { callback && callback(null, tokenResponse.error); return; }
                const token = localStorage.getItem('pepperJwt');
                if (!token) return;
                try {
                    const res = await fetch('/api/account/link-google', {
                        method: 'POST',
                        headers: { 'Authorization': `Bearer ${token}`, 'Content-Type': 'application/json' },
                        body: JSON.stringify({ token: tokenResponse.access_token })
                    });
                    const data = await res.json().catch(() => ({}));
                    if (!res.ok) throw new Error(data.detail || 'Failed to link Google');
                    localStorage.setItem('pepperGoogleLinked', 'true');
                    localStorage.setItem('pepperAuthProvider', data.auth_provider || localStorage.getItem('pepperAuthProvider') || 'local');
                    if (data.google_email) localStorage.setItem('pepperGoogleEmail', data.google_email);
                    renderProfileSection();
                } catch (err) {
                    alert(err.message);
                }
            }
        });
        googleLinkTokenClient.requestAccessToken();
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
                const token = localStorage.getItem('pepperJwt');
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
