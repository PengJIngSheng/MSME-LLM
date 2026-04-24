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

const GUEST_QUESTION_LIMIT = 2;
const agentModeBtn = document.getElementById('agentModeBtn');
const guestLimitBanner = document.getElementById('guestLimitBanner');
const guestLimitBannerText = document.getElementById('guestLimitBannerText');
const guestLimitLoginBtn = document.getElementById('guestLimitLoginBtn');
const guestLimitRegisterBtn = document.getElementById('guestLimitRegisterBtn');
const appContainer = document.querySelector('.app-container');
const inputWrapper = document.querySelector('.input-wrapper');
const liquidGlassInput = document.querySelector('.liquid-glass-input');

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
        loginBtn: 'Login',
        registerBtn: 'Register',
        guestLimitText: 'Get smarter responses, upload files and images, and unlock more features.',
        guestLimitRegisterBtn: 'Sign up for free',
        agentRequiresLogin: 'Login required for Agent mode'
    };
}

function updateGuestLimitBannerCopy() {
    const t = getUiCopy();
    if (guestLimitBannerText) guestLimitBannerText.textContent = t.guestLimitText;
    if (guestLimitLoginBtn) guestLimitLoginBtn.textContent = t.loginBtn;
    if (guestLimitRegisterBtn) guestLimitRegisterBtn.textContent = t.guestLimitRegisterBtn || t.registerBtn;
    if (agentModeBtn) agentModeBtn.title = currentUserId ? 'AI Agent' : (t.agentRequiresLogin || 'Login required for Agent mode');
}

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
    guestLimitBanner.classList.remove('attention');
    void guestLimitBanner.offsetWidth;
    guestLimitBanner.classList.add('attention');
    setTimeout(() => {
        guestLimitBanner.classList.remove('attention');
    }, 1200);
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
        const res = await fetch(`/api/history/${chatId}`);
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
    logoContainer.innerHTML = '<h2><span class="logo-text"><i class="fa-solid fa-leaf"></i> PEPPER LABS</span><br/>How can I help you today?</h2>';
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
    liquidGlassInput.addEventListener('mousedown', lockedGuestAgentHandler, true);
    liquidGlassInput.addEventListener('click', lockedGuestAgentHandler, true);
    liquidGlassInput.addEventListener('touchstart', lockedGuestAgentHandler, true);
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
        checkbox.checked = data.active;
    }
}

async function fetchConnectorsStatus() {
    // Deliberately disabled per user instruction.
    // Connectors will now always default to unchecked (OFF) upon login/page reload.
    return;
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
            client_id: '685645444928-ivt7lgsjiatv0ff0r68ckmbln1rdrrm4.apps.googleusercontent.com',
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
                            showToast("Your session expired during auth. Please log in again.", true);
                            setTimeout(() => window.location.href = '/static/login.html', 1500);
                            return;
                        }
                        
                        const data = await res.json();
                        if (res.ok && data.status === "success") {
                            // Manually turn switch ON since we no longer sync state globally
                            const checkbox = document.getElementById(`checkbox-${pendingOAuthService}`);
                            if(checkbox) checkbox.checked = true;
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
    'calendar': 'https://www.googleapis.com/auth/calendar.events'
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
                
                await fetch('/api/connectors/toggle', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'Authorization': `Bearer ${token}`
                    },
                    body: JSON.stringify({ service: service, enabled: willBeActive })
                });
                // No status fetch!
            } catch (err) {
                console.error("Toggle failed", err);
                // Revert UI on failure
                checkbox.checked = !willBeActive;
            }
        }
    });
});

// Init Connectors UI data and build 4 background OAuth clients
setTimeout(async () => {
    // Wipe all old Google credentials on page load so user must re-authorize
    const token = localStorage.getItem('pepperJwt');
    if (token) {
        try {
            await fetch('/api/connectors/clear_all', {
                method: 'POST',
                headers: { 'Authorization': `Bearer ${token}` }
            });
        } catch (e) { /* ignore */ }
    }
    initGoogleClients();
}, 2000);

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
        userInput.style.height = 'auto';

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
userInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); handleSend(false, null); }
});

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

// Open Search Modal
openSearchModalBtn.addEventListener('click', async () => {
    searchHistoryModal.classList.add('show');
    historySearchInput.value = '';
    
    // Reset preview
    previewEmptyState.classList.remove('hidden');
    previewContent.classList.add('hidden');
    currentPreviewChatId = null;
    document.querySelectorAll('.history-search-list .history-item').forEach(el => el.classList.remove('active-preview'));

    // Fetch History
    try {
        const fetchUrl = currentUserId ? `/api/history?user_id=${currentUserId}` : '/api/history';
        const res = await fetch(fetchUrl);
        const data = await res.json();
                if (data.chats) {
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
                if (chatDate.getTime() === today.getTime()) groupName = "Today";
                else if (chatDate.getTime() === yesterday.getTime()) groupName = "Yesterday";
                else {
                    const diffTime = Math.abs(today - chatDate);
                    const diffDays = Math.ceil(diffTime / (1000 * 60 * 60 * 24));
                    if (diffDays <= 7) groupName = "Previous 7 Days";
                    else if (diffDays <= 30) groupName = "Previous 30 Days";
                    else {
                        const year = date.getFullYear();
                        const month = String(date.getMonth() + 1).padStart(2, '0');
                        groupName = `${year}-${month}`;
                    }
                }
                if (!groups[groupName]) groups[groupName] = [];
                groups[groupName].push({ id: chat._id, title: chat.title || 'New Chat' });
            });
            renderSearchModalHistory(groups);
        } else {
            modalHistoryList.innerHTML = `<li class="history-placeholder">Failed to load history</li>`;
        }
    } catch (e) {
        modalHistoryList.innerHTML = `<li class="history-placeholder">Failed connecting to server</li>`;
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
});

function renderSearchModalHistory(groups) {
    modalHistoryList.innerHTML = '';
    if (Object.keys(groups).length === 0) {
        modalHistoryList.innerHTML = `<li class="history-placeholder"><span class="nav-text">No conversation history yet.</span></li>`;
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
            
            li.innerHTML = `
                <div class="history-item-text" title="${entry.title}">${entry.title}</div>
                <div class="history-item-actions">
                    <button class="modal-action-btn open-btn" title="Open"><i class="fa-solid fa-arrow-up-right-from-square" style="font-size: 0.8rem;"></i></button>
                    <button class="modal-action-btn edit-btn" title="Edit"><i class="fa-solid fa-pencil" style="font-size: 0.8rem;"></i></button>
                    <button class="modal-action-btn delete-btn" title="Delete"><i class="fa-regular fa-trash-can" style="font-size: 0.8rem;"></i></button>
                </div>
            `;
            
            const openBtn = li.querySelector('.open-btn');
            const editBtn = li.querySelector('.edit-btn');
            const deleteBtn = li.querySelector('.delete-btn');
            const textDiv = li.querySelector('.history-item-text');
            
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
    if (currentPreviewChatId === chatId) return; // already viewing
    
    // highlight actively previewed item
    document.querySelectorAll('.history-search-list .history-item').forEach(el => el.classList.remove('active-preview'));
    liElement.classList.add('active-preview');
    
    currentPreviewChatId = chatId;
    previewEmptyState.classList.add('hidden');
    previewContent.classList.remove('hidden');
    previewTitle.innerText = title;
    previewMessages.innerHTML = `<div style="color:var(--primary-dim);text-align:center;margin-top:20px;">Fetching logs...</div>`;
    
    try {
        const res = await fetch(`/api/history/${chatId}`);
        const data = await res.json();
        if (res.ok && data.chat) {
            const msgs = data.chat.messages || [];
            previewMessages.innerHTML = '';
            
            if (msgs.length === 0) {
                previewMessages.innerHTML = `<div style="color:var(--primary-dim);">This chat is empty.</div>`;
                return;
            }
            
            msgs.forEach(m => {
                const wrapper = document.createElement('div');
                wrapper.className = `msg-wrapper ${m.role}`;
                
                const contentDiv = document.createElement('div');
                contentDiv.className = `msg-content ${m.role}`;
                
                if (m.role === 'assistant') {
                    // Strip huge <think> blocks for preview clarity
                    let content = m.content;
                    content = content.replace(/<think>[\s\S]*?<\/think>/g, '<div style="color:var(--primary-dim); font-size: 0.8rem; font-style: italic; margin-bottom: 8px;">[Thought process completed]</div>');
                    contentDiv.innerHTML = marked.parse(content);
                } else {
                    contentDiv.innerText = m.content;
                }
                
                wrapper.appendChild(contentDiv);
                previewMessages.appendChild(wrapper);
            });
        } else {
            previewMessages.innerHTML = `<div style="color:var(--primary-dim);">Failed to parse chat logs.</div>`;
        }
    } catch(e) {
        previewMessages.innerHTML = `<div style="color:var(--primary-dim);">Failed to fetch preview.</div>`;
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
            nav.querySelectorAll('.theme-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            applyTheme(btn.dataset.theme);
            localStorage.setItem('pepperTheme', btn.dataset.theme);
        });
    });

    function applyTheme(theme) {
        if (theme === 'dark') {
            document.documentElement.setAttribute('data-theme', 'dark');
            
        } else {
            document.documentElement.removeAttribute('data-theme');
            
        }
    }

    // ── Language Selector ──
    const savedLang = localStorage.getItem('pepperLang') || 'en';
    applyLang(savedLang);

    nav.querySelectorAll('.lang-btn').forEach(btn => {
        if (btn.dataset.lang === savedLang) btn.classList.add('active');
        else btn.classList.remove('active');

        btn.addEventListener('click', () => {
            nav.querySelectorAll('.lang-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            applyLang(btn.dataset.lang);
            localStorage.setItem('pepperLang', btn.dataset.lang);
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
                ctxHelp: 'Help',
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
                ctxHelp: '帮助',
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
                ctxHelp: 'Bantuan',
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
        const ctxFilesTxt = document.querySelectorAll('.ctx-menu-item span')[1];
        if (ctxFilesTxt && !ctxFilesTxt.closest('#ctxSettingsBlock') && !ctxFilesTxt.closest('#ctxLogoutBtn')) ctxFilesTxt.textContent = t.ctxFiles;
        const ctxHelpTxt = document.querySelectorAll('.ctx-menu-item span')[2];
        if (ctxHelpTxt && !ctxHelpTxt.closest('#ctxLogoutBtn')) ctxHelpTxt.textContent = t.ctxHelp;
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
        updateGuestLimitBannerCopy();
    }
})();

// ============ Sidebar User Context Menu & Avatar Sync ============
(function initUserContextMenu() {
    const showLoginBtn = document.getElementById('showLoginBtn');
    const contextMenu = document.getElementById('userContextMenu');
    const avatarDisplay = document.getElementById('avatarDisplay');
    const userDisplayName = document.getElementById('userDisplayName');
    const userEmailDisplay = document.getElementById('userEmailDisplay');
    const logoutBtn = document.getElementById('ctxLogoutBtn');
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
            localStorage.removeItem('pepperAvatar');
            localStorage.removeItem('pepperUserId');
            return;
        }
        
        if (username && username.includes('@')) {
            if (userDisplayName) userDisplayName.innerText = username.split('@')[0];
            if (userEmailDisplay) userEmailDisplay.innerText = username;
            
            if (avatarUrl && avatarDisplay) {
                avatarDisplay.innerHTML = `<img src="${avatarUrl}" alt="Avatar">`;
                avatarDisplay.style.background = 'transparent';
            } else if (avatarDisplay) {
                const firstLetter = username.charAt(0).toUpperCase();
                const bgColor = hashToHSL(username);
                avatarDisplay.innerHTML = firstLetter;
                avatarDisplay.style.background = `linear-gradient(135deg, ${bgColor}, #333333)`;
            }
        } else if (username) {
            if (userDisplayName) userDisplayName.innerText = username;
            if (userEmailDisplay) userEmailDisplay.innerText = '';
            
            if (avatarDisplay) {
                const firstLetter = username.charAt(0).toUpperCase();
                const bgColor = hashToHSL(username);
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
            localStorage.removeItem('pepperAvatar');
            localStorage.removeItem('pepperGuestQuestionCount');
            window.location.href = '/static/login.html';
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
                ctxThemeGroup.querySelectorAll('.theme-btn').forEach(b => b.classList.remove('active'));
                btn.classList.add('active');
                if (btn.dataset.theme === 'dark') {
                    document.documentElement.setAttribute('data-theme', 'dark');
                    
                } else {
                    document.documentElement.removeAttribute('data-theme');
                    
                }
                localStorage.setItem('pepperTheme', btn.dataset.theme);
                
                // Update top guest nav as well to keep sync if visible
                const guestThemeGroup = document.getElementById('themeToggleGroup');
                if(guestThemeGroup) {
                    guestThemeGroup.querySelectorAll('.theme-btn').forEach(b => {
                        b.classList.toggle('active', b.dataset.theme === btn.dataset.theme);
                    });
                }
            });
        });
    }
    
    const ctxLangSel = document.getElementById('ctxLangSelector');
    if (ctxLangSel) {
        ctxLangSel.querySelectorAll('.lang-btn').forEach(btn => {
            const currentLang = localStorage.getItem('pepperLang') || 'en';
            btn.classList.toggle('active', btn.dataset.lang === currentLang);
            btn.addEventListener('click', () => {
                ctxLangSel.querySelectorAll('.lang-btn').forEach(b => b.classList.remove('active'));
                btn.classList.add('active');
                localStorage.setItem('pepperLang', btn.dataset.lang);
                // Call global reload for language change!
                window.location.reload();
            });
        });
    }
})();
