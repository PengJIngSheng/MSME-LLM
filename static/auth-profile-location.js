(function () {
    const COUNTRY_OPTIONS = [
        { name: 'Malaysia', iso: 'MY', dial: '+60', flag: '🇲🇾' },
        { name: 'Singapore', iso: 'SG', dial: '+65', flag: '🇸🇬' },
        { name: 'China', iso: 'CN', dial: '+86', flag: '🇨🇳' },
        { name: 'United States', iso: 'US', dial: '+1', flag: '🇺🇸' },
        { name: 'United Kingdom', iso: 'GB', dial: '+44', flag: '🇬🇧' },
        { name: 'Australia', iso: 'AU', dial: '+61', flag: '🇦🇺' },
        { name: 'Indonesia', iso: 'ID', dial: '+62', flag: '🇮🇩' },
        { name: 'Thailand', iso: 'TH', dial: '+66', flag: '🇹🇭' },
        { name: 'Vietnam', iso: 'VN', dial: '+84', flag: '🇻🇳' },
        { name: 'Philippines', iso: 'PH', dial: '+63', flag: '🇵🇭' },
        { name: 'Japan', iso: 'JP', dial: '+81', flag: '🇯🇵' },
        { name: 'South Korea', iso: 'KR', dial: '+82', flag: '🇰🇷' },
        { name: 'Hong Kong', iso: 'HK', dial: '+852', flag: '🇭🇰' },
        { name: 'Taiwan', iso: 'TW', dial: '+886', flag: '🇹🇼' },
        { name: 'India', iso: 'IN', dial: '+91', flag: '🇮🇳' },
        { name: 'Canada', iso: 'CA', dial: '+1', flag: '🇨🇦' },
        { name: 'New Zealand', iso: 'NZ', dial: '+64', flag: '🇳🇿' },
        { name: 'United Arab Emirates', iso: 'AE', dial: '+971', flag: '🇦🇪' }
    ];

    const REGIONS_BY_COUNTRY = {
        Malaysia: [
            'Johor', 'Kedah', 'Kelantan', 'Melaka', 'Negeri Sembilan', 'Pahang',
            'Penang', 'Perak', 'Perlis', 'Sabah', 'Sarawak', 'Selangor',
            'Terengganu', 'Kuala Lumpur', 'Labuan', 'Putrajaya'
        ],
        Singapore: ['Central Region', 'East Region', 'North Region', 'North-East Region', 'West Region'],
        China: [
            'Anhui', 'Beijing', 'Chongqing', 'Fujian', 'Gansu', 'Guangdong',
            'Guangxi', 'Guizhou', 'Hainan', 'Hebei', 'Heilongjiang', 'Henan',
            'Hubei', 'Hunan', 'Inner Mongolia', 'Jiangsu', 'Jiangxi', 'Jilin',
            'Liaoning', 'Ningxia', 'Qinghai', 'Shaanxi', 'Shandong', 'Shanghai',
            'Shanxi', 'Sichuan', 'Tianjin', 'Tibet', 'Xinjiang', 'Yunnan', 'Zhejiang'
        ],
        'United States': [
            'Alabama', 'Alaska', 'Arizona', 'Arkansas', 'California', 'Colorado',
            'Connecticut', 'Delaware', 'District of Columbia', 'Florida', 'Georgia',
            'Hawaii', 'Idaho', 'Illinois', 'Indiana', 'Iowa', 'Kansas', 'Kentucky',
            'Louisiana', 'Maine', 'Maryland', 'Massachusetts', 'Michigan',
            'Minnesota', 'Mississippi', 'Missouri', 'Montana', 'Nebraska', 'Nevada',
            'New Hampshire', 'New Jersey', 'New Mexico', 'New York', 'North Carolina',
            'North Dakota', 'Ohio', 'Oklahoma', 'Oregon', 'Pennsylvania',
            'Rhode Island', 'South Carolina', 'South Dakota', 'Tennessee', 'Texas',
            'Utah', 'Vermont', 'Virginia', 'Washington', 'West Virginia',
            'Wisconsin', 'Wyoming'
        ],
        'United Kingdom': ['England', 'Scotland', 'Wales', 'Northern Ireland'],
        Australia: [
            'Australian Capital Territory', 'New South Wales', 'Northern Territory',
            'Queensland', 'South Australia', 'Tasmania', 'Victoria', 'Western Australia'
        ],
        Indonesia: [
            'Aceh', 'Bali', 'Banten', 'Bengkulu', 'Central Java', 'Central Kalimantan',
            'Central Sulawesi', 'East Java', 'East Kalimantan', 'East Nusa Tenggara',
            'Gorontalo', 'Highland Papua', 'Jakarta', 'Jambi', 'Lampung', 'Maluku',
            'North Kalimantan', 'North Maluku', 'North Sulawesi', 'North Sumatra',
            'Papua', 'Riau', 'Riau Islands', 'South Kalimantan', 'South Papua',
            'South Sulawesi', 'South Sumatra', 'Southeast Sulawesi', 'Southwest Papua',
            'West Java', 'West Kalimantan', 'West Nusa Tenggara', 'West Papua',
            'West Sulawesi', 'West Sumatra', 'Yogyakarta'
        ],
        Thailand: [
            'Amnat Charoen', 'Ang Thong', 'Bangkok', 'Bueng Kan', 'Buri Ram',
            'Chachoengsao', 'Chai Nat', 'Chaiyaphum', 'Chanthaburi', 'Chiang Mai',
            'Chiang Rai', 'Chon Buri', 'Chumphon', 'Kalasin', 'Kamphaeng Phet',
            'Kanchanaburi', 'Khon Kaen', 'Krabi', 'Lampang', 'Lamphun', 'Loei',
            'Lop Buri', 'Mae Hong Son', 'Maha Sarakham', 'Mukdahan', 'Nakhon Nayok',
            'Nakhon Pathom', 'Nakhon Phanom', 'Nakhon Ratchasima', 'Nakhon Sawan',
            'Nakhon Si Thammarat', 'Nan', 'Narathiwat', 'Nong Bua Lam Phu',
            'Nong Khai', 'Nonthaburi', 'Pathum Thani', 'Pattani', 'Phang Nga',
            'Phatthalung', 'Phayao', 'Phetchabun', 'Phetchaburi', 'Phichit',
            'Phitsanulok', 'Phra Nakhon Si Ayutthaya', 'Phrae', 'Phuket',
            'Prachin Buri', 'Prachuap Khiri Khan', 'Ranong', 'Ratchaburi',
            'Rayong', 'Roi Et', 'Sa Kaeo', 'Sakon Nakhon', 'Samut Prakan',
            'Samut Sakhon', 'Samut Songkhram', 'Saraburi', 'Satun', 'Sing Buri',
            'Sisaket', 'Songkhla', 'Sukhothai', 'Suphan Buri', 'Surat Thani',
            'Surin', 'Tak', 'Trang', 'Trat', 'Ubon Ratchathani', 'Udon Thani',
            'Uthai Thani', 'Uttaradit', 'Yala', 'Yasothon'
        ],
        Vietnam: [
            'An Giang', 'Bac Ninh', 'Ca Mau', 'Can Tho', 'Cao Bang', 'Da Nang',
            'Dak Lak', 'Dien Bien', 'Dong Nai', 'Dong Thap', 'Gia Lai', 'Ha Noi',
            'Ha Tinh', 'Hai Phong', 'Ho Chi Minh City', 'Hue', 'Hung Yen',
            'Khanh Hoa', 'Lai Chau', 'Lam Dong', 'Lang Son', 'Lao Cai',
            'Nghe An', 'Ninh Binh', 'Phu Tho', 'Quang Ngai', 'Quang Ninh',
            'Quang Tri', 'Son La', 'Tay Ninh', 'Thai Nguyen', 'Thanh Hoa',
            'Tuyen Quang', 'Vinh Long'
        ],
        Philippines: [
            'Abra', 'Agusan del Norte', 'Agusan del Sur', 'Aklan', 'Albay',
            'Antique', 'Apayao', 'Aurora', 'Basilan', 'Bataan', 'Batanes',
            'Batangas', 'Benguet', 'Biliran', 'Bohol', 'Bukidnon', 'Bulacan',
            'Cagayan', 'Camarines Norte', 'Camarines Sur', 'Camiguin', 'Capiz',
            'Catanduanes', 'Cavite', 'Cebu', 'Cotabato', 'Davao de Oro',
            'Davao del Norte', 'Davao del Sur', 'Davao Occidental', 'Davao Oriental',
            'Dinagat Islands', 'Eastern Samar', 'Guimaras', 'Ifugao', 'Ilocos Norte',
            'Ilocos Sur', 'Iloilo', 'Isabela', 'Kalinga', 'La Union', 'Laguna',
            'Lanao del Norte', 'Lanao del Sur', 'Leyte', 'Maguindanao del Norte',
            'Maguindanao del Sur', 'Marinduque', 'Masbate', 'Metro Manila',
            'Misamis Occidental', 'Misamis Oriental', 'Mountain Province',
            'Negros Occidental', 'Negros Oriental', 'Northern Samar', 'Nueva Ecija',
            'Nueva Vizcaya', 'Occidental Mindoro', 'Oriental Mindoro', 'Palawan',
            'Pampanga', 'Pangasinan', 'Quezon', 'Quirino', 'Rizal', 'Romblon',
            'Samar', 'Sarangani', 'Siquijor', 'Sorsogon', 'South Cotabato',
            'Southern Leyte', 'Sultan Kudarat', 'Sulu', 'Surigao del Norte',
            'Surigao del Sur', 'Tarlac', 'Tawi-Tawi', 'Zambales', 'Zamboanga del Norte',
            'Zamboanga del Sur', 'Zamboanga Sibugay'
        ],
        Japan: [
            'Aichi', 'Akita', 'Aomori', 'Chiba', 'Ehime', 'Fukui', 'Fukuoka',
            'Fukushima', 'Gifu', 'Gunma', 'Hiroshima', 'Hokkaido', 'Hyogo',
            'Ibaraki', 'Ishikawa', 'Iwate', 'Kagawa', 'Kagoshima', 'Kanagawa',
            'Kochi', 'Kumamoto', 'Kyoto', 'Mie', 'Miyagi', 'Miyazaki', 'Nagano',
            'Nagasaki', 'Nara', 'Niigata', 'Oita', 'Okayama', 'Okinawa', 'Osaka',
            'Saga', 'Saitama', 'Shiga', 'Shimane', 'Shizuoka', 'Tochigi',
            'Tokushima', 'Tokyo', 'Tottori', 'Toyama', 'Wakayama', 'Yamagata',
            'Yamaguchi', 'Yamanashi'
        ],
        'South Korea': [
            'Busan', 'Chungcheongbuk-do', 'Chungcheongnam-do', 'Daegu', 'Daejeon',
            'Gangwon-do', 'Gwangju', 'Gyeonggi-do', 'Gyeongsangbuk-do',
            'Gyeongsangnam-do', 'Incheon', 'Jeju-do', 'Jeollabuk-do',
            'Jeollanam-do', 'Sejong', 'Seoul', 'Ulsan'
        ],
        'Hong Kong': ['Hong Kong Island', 'Kowloon', 'New Territories'],
        Taiwan: [
            'Changhua', 'Chiayi City', 'Chiayi County', 'Hsinchu City',
            'Hsinchu County', 'Hualien', 'Kaohsiung', 'Keelung', 'Kinmen',
            'Lienchiang', 'Miaoli', 'Nantou', 'New Taipei', 'Penghu', 'Pingtung',
            'Taichung', 'Tainan', 'Taipei', 'Taitung', 'Taoyuan', 'Yilan', 'Yunlin'
        ],
        India: [
            'Andaman and Nicobar Islands', 'Andhra Pradesh', 'Arunachal Pradesh',
            'Assam', 'Bihar', 'Chandigarh', 'Chhattisgarh', 'Dadra and Nagar Haveli and Daman and Diu',
            'Delhi', 'Goa', 'Gujarat', 'Haryana', 'Himachal Pradesh', 'Jammu and Kashmir',
            'Jharkhand', 'Karnataka', 'Kerala', 'Ladakh', 'Lakshadweep',
            'Madhya Pradesh', 'Maharashtra', 'Manipur', 'Meghalaya', 'Mizoram',
            'Nagaland', 'Odisha', 'Puducherry', 'Punjab', 'Rajasthan', 'Sikkim',
            'Tamil Nadu', 'Telangana', 'Tripura', 'Uttar Pradesh', 'Uttarakhand',
            'West Bengal'
        ],
        Canada: [
            'Alberta', 'British Columbia', 'Manitoba', 'New Brunswick',
            'Newfoundland and Labrador', 'Northwest Territories', 'Nova Scotia',
            'Nunavut', 'Ontario', 'Prince Edward Island', 'Quebec', 'Saskatchewan',
            'Yukon'
        ],
        'New Zealand': [
            'Auckland', 'Bay of Plenty', 'Canterbury', 'Gisborne', "Hawke's Bay",
            'Manawatu-Whanganui', 'Marlborough', 'Nelson', 'Northland', 'Otago',
            'Southland', 'Taranaki', 'Tasman', 'Waikato', 'Wellington', 'West Coast'
        ],
        'United Arab Emirates': [
            'Abu Dhabi', 'Ajman', 'Dubai', 'Fujairah', 'Ras Al Khaimah',
            'Sharjah', 'Umm Al Quwain'
        ]
    };

    const DEFAULT_COUNTRY = COUNTRY_OPTIONS[0];
    const countriesByIso = new Map(COUNTRY_OPTIONS.map((country) => [country.iso, country]));
    const countriesByName = new Map(COUNTRY_OPTIONS.map((country) => [country.name.toLowerCase(), country]));

    function flagMarkup(country) {
        return `<span class="flag has-emoji" aria-hidden="true">${country.flag || ''}</span>`;
    }

    function setFlagElement(element, country) {
        if (!element) return;
        if (!country) {
            element.textContent = '';
            element.style.backgroundImage = '';
            element.classList.add('is-empty');
            element.classList.remove('has-emoji');
            element.removeAttribute('aria-label');
            element.removeAttribute('title');
            return;
        }
        element.textContent = country.flag || '';
        element.style.backgroundImage = '';
        element.classList.remove('is-empty');
        element.classList.add('has-emoji');
        element.setAttribute('aria-label', `${country.name} flag`);
        element.title = country.name;
    }

    function findCountry(value) {
        const raw = (value || '').trim();
        if (!raw) return null;
        const upper = raw.toUpperCase();
        if (countriesByIso.has(upper)) return countriesByIso.get(upper);
        if (countriesByName.has(raw.toLowerCase())) return countriesByName.get(raw.toLowerCase());
        const byDial = COUNTRY_OPTIONS.find((country) => country.dial === raw);
        return byDial || null;
    }

    function countryFrom(value) {
        return findCountry(value) || DEFAULT_COUNTRY;
    }

    function getRegions(countryName) {
        const regions = REGIONS_BY_COUNTRY[countryName];
        return regions && regions.length ? regions : [countryName, 'Other'];
    }

    function formatStoredRegion(countryName, regionName) {
        const region = (regionName || '').trim();
        if (!region) return countryName;
        if (region === countryName) return countryName;
        if (region.startsWith(`${countryName} - `)) return region;
        return `${countryName} - ${region}`;
    }

    function displayRegionFromStored(countryName, storedRegion) {
        const regions = getRegions(countryName);
        const raw = (storedRegion || '').trim();
        if (!raw || raw === countryName) return regions[0] || countryName;
        const prefix = `${countryName} - `;
        const candidate = raw.startsWith(prefix) ? raw.slice(prefix.length) : raw;
        return regions.includes(candidate) ? candidate : (regions[0] || countryName);
    }

    function countryFromStoredRegion(storedRegion) {
        const raw = (storedRegion || '').trim().toLowerCase();
        if (!raw) return null;
        return COUNTRY_OPTIONS.find((country) => raw === country.name.toLowerCase() || raw.startsWith(`${country.name.toLowerCase()} - `)) || null;
    }

    const mobileSelectQuery = window.matchMedia('(max-width: 520px), (pointer: coarse) and (max-height: 520px)');
    let mobileSelectRoot = null;
    let mobileSelectRestoreFocus = true;

    function mobileSelectCopy(kind) {
        const language = (document.documentElement.lang || 'en').toLowerCase().split('-')[0];
        const copy = {
            en: { country: 'Select country or region', region: 'Select region', close: 'Close' },
            zh: { country: '选择国家或地区', region: '选择区域', close: '关闭' },
            ms: { country: 'Pilih negara atau rantau', region: 'Pilih wilayah', close: 'Tutup' }
        }[language] || { country: 'Select country or region', region: 'Select region', close: 'Close' };
        return { title: copy[kind === 'region' ? 'region' : 'country'], close: copy.close };
    }

    function ensureMobileSelectDialog() {
        let dialog = document.getElementById('authSelectDialog');
        if (dialog) return dialog;

        dialog = document.createElement('dialog');
        dialog.id = 'authSelectDialog';
        dialog.className = 'auth-select-dialog';
        dialog.setAttribute('aria-labelledby', 'authSelectDialogTitle');
        dialog.innerHTML = `
            <section class="auth-select-sheet">
                <header class="auth-select-sheet-header">
                    <h2 class="auth-select-sheet-title" id="authSelectDialogTitle"></h2>
                    <button type="button" class="auth-select-sheet-close" aria-label="Close" title="Close">
                        <i class="fa-solid fa-xmark" aria-hidden="true"></i>
                    </button>
                </header>
                <div class="auth-select-sheet-options" role="listbox"></div>
            </section>
        `;
        document.body.appendChild(dialog);

        dialog.querySelector('.auth-select-sheet-close').addEventListener('click', () => closeMobileSelectDialog());
        dialog.addEventListener('cancel', (event) => {
            event.preventDefault();
            closeMobileSelectDialog();
        });
        dialog.addEventListener('click', (event) => {
            if (event.target === dialog) closeMobileSelectDialog();
        });
        dialog.addEventListener('close', () => {
            const previousRoot = mobileSelectRoot;
            const trigger = previousRoot?.querySelector('.custom-select-trigger');
            previousRoot?.classList.remove('open', 'mobile-sheet-open');
            trigger?.setAttribute('aria-expanded', 'false');
            dialog.classList.remove('is-closing');
            dialog.querySelector('.auth-select-sheet-options').replaceChildren();
            document.body.classList.remove('auth-select-dialog-open');
            mobileSelectRoot = null;
            if (mobileSelectRestoreFocus && trigger?.isConnected) {
                window.requestAnimationFrame(() => trigger.focus({ preventScroll: true }));
            }
            mobileSelectRestoreFocus = true;
        });
        return dialog;
    }

    function finishMobileSelectClose(dialog) {
        if (dialog.open) dialog.close();
    }

    function closeMobileSelectDialog({ restoreFocus = true, immediate = false } = {}) {
        const dialog = document.getElementById('authSelectDialog');
        if (!dialog?.open) return;
        mobileSelectRestoreFocus = restoreFocus;
        const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
        if (immediate || reducedMotion) {
            finishMobileSelectClose(dialog);
            return;
        }
        if (dialog.classList.contains('is-closing')) return;
        dialog.classList.add('is-closing');
        const sheet = dialog.querySelector('.auth-select-sheet');
        const finish = () => {
            if (dialog.classList.contains('is-closing')) finishMobileSelectClose(dialog);
        };
        sheet.addEventListener('animationend', finish, { once: true });
        // Animation events can be suppressed by browser lifecycle changes.
        window.setTimeout(finish, 260);
    }

    function openMobileSelectDialog(root) {
        const dialog = ensureMobileSelectDialog();
        const trigger = root.querySelector('.custom-select-trigger');
        const sourceMenu = root.querySelector('.custom-select-menu');
        if (!trigger || !sourceMenu || typeof dialog.showModal !== 'function') return false;

        if (dialog.open) closeMobileSelectDialog({ restoreFocus: false, immediate: true });
        mobileSelectRoot = root;
        mobileSelectRestoreFocus = true;
        root.classList.remove('open');
        root.classList.add('mobile-sheet-open');
        trigger.setAttribute('aria-expanded', 'true');
        trigger.setAttribute('aria-controls', dialog.id);

        const kind = root.dataset.selectKind || (root.classList.contains('region-select') ? 'region' : 'country');
        const copy = mobileSelectCopy(kind);
        const title = dialog.querySelector('.auth-select-sheet-title');
        const closeButton = dialog.querySelector('.auth-select-sheet-close');
        const optionsHost = dialog.querySelector('.auth-select-sheet-options');
        title.textContent = copy.title;
        closeButton.setAttribute('aria-label', copy.close);
        closeButton.title = copy.close;
        optionsHost.setAttribute('aria-label', copy.title);
        optionsHost.replaceChildren();

        Array.from(sourceMenu.querySelectorAll('.custom-option')).forEach((sourceOption) => {
            const option = sourceOption.cloneNode(true);
            option.addEventListener('click', () => {
                const currentOption = Array.from(sourceMenu.querySelectorAll('.custom-option'))
                    .find((candidate) => candidate.dataset.value === sourceOption.dataset.value);
                currentOption?.click();
            });
            optionsHost.appendChild(option);
        });

        document.body.classList.add('auth-select-dialog-open');
        dialog.classList.remove('is-closing');
        dialog.showModal();
        window.requestAnimationFrame(() => {
            const selected = optionsHost.querySelector('[aria-selected="true"]') || optionsHost.querySelector('.custom-option');
            selected?.focus({ preventScroll: true });
            selected?.scrollIntoView({ block: 'nearest' });
        });
        return true;
    }

    function closeAllSelects(exceptRoot) {
        if (mobileSelectRoot && mobileSelectRoot !== exceptRoot) closeMobileSelectDialog();
        document.querySelectorAll('.custom-select.open, .custom-select.mobile-sheet-open').forEach((root) => {
            if (root === exceptRoot) return;
            root.classList.remove('open', 'mobile-sheet-open');
            const trigger = root.querySelector('.custom-select-trigger');
            if (trigger) trigger.setAttribute('aria-expanded', 'false');
        });
    }

    function toggleSelect(root, open) {
        const isOpen = root.classList.contains('open') || root.classList.contains('mobile-sheet-open');
        const shouldOpen = typeof open === 'boolean' ? open : !isOpen;
        closeAllSelects(shouldOpen ? root : null);
        if (shouldOpen && mobileSelectQuery.matches && openMobileSelectDialog(root)) return;
        if (!shouldOpen && mobileSelectRoot === root) {
            closeMobileSelectDialog();
            return;
        }
        root.classList.remove('mobile-sheet-open');
        root.classList.toggle('open', shouldOpen);
        const trigger = root.querySelector('.custom-select-trigger');
        if (trigger) trigger.setAttribute('aria-expanded', shouldOpen ? 'true' : 'false');
    }

    function bindGlobalClose() {
        if (window.__msmeProfileSelectGlobalCloseBound) return;
        window.__msmeProfileSelectGlobalCloseBound = true;
        document.addEventListener('click', (event) => {
            if (!event.target.closest('.custom-select')) closeAllSelects();
        });
        document.addEventListener('keydown', (event) => {
            if (event.key === 'Escape') closeAllSelects();
        });
        mobileSelectQuery.addEventListener?.('change', () => {
            if (mobileSelectRoot) closeMobileSelectDialog({ immediate: true });
            closeAllSelects();
        });
    }

    function renderCountryOption(country, selectedIso, onSelect) {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'custom-option';
        button.setAttribute('role', 'option');
        button.setAttribute('aria-selected', country.iso === selectedIso ? 'true' : 'false');
        button.dataset.value = country.iso;
        button.innerHTML = `
            ${flagMarkup(country)}
            <span class="option-copy">
                <span class="option-main">${country.name}</span>
                <span class="option-sub">${country.iso} ${country.dial}</span>
            </span>
        `;
        button.addEventListener('click', () => onSelect(country));
        return button;
    }

    function renderRegionOption(country, region, selectedRegion, onSelect) {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'custom-option';
        button.setAttribute('role', 'option');
        button.setAttribute('aria-selected', region === selectedRegion ? 'true' : 'false');
        button.dataset.value = region;
        button.innerHTML = `
            <span class="option-copy">
                <span class="option-main">${region}</span>
                <span class="option-sub">${country.name}</span>
            </span>
        `;
        button.addEventListener('click', () => onSelect(region));
        return button;
    }

    function getGeoDefaults() {
        if (window.__msmeGeoDefaultsPromise) return window.__msmeGeoDefaultsPromise;
        const params = new URLSearchParams({
            timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || '',
            browser_language: navigator.language || ''
        });
        window.__msmeGeoDefaultsPromise = fetch(`/api/geo/location?${params.toString()}`, { cache: 'no-store' })
            .then((res) => (res.ok ? res.json() : null))
            .catch(() => null);
        return window.__msmeGeoDefaultsPromise;
    }

    function initPhoneRegionForm(options = {}) {
        const getStored = (key) => (options.useStoredProfile === false ? '' : localStorage.getItem(key));
        const countryCodeInput = document.getElementById(options.countryCodeInputId || 'googleProfileCountryCode');
        const countryInput = document.getElementById(options.countryInputId || 'googleProfileCountry');
        const countryIsoInput = document.getElementById(options.countryIsoInputId || 'googleProfileCountryIso');
        const regionInput = document.getElementById(options.regionInputId || 'googleProfileRegion');
        const phoneRoot = document.getElementById(options.phoneRootId || 'phoneCountrySelect');
        const phoneTrigger = document.getElementById(options.phoneTriggerId || 'phoneCountryTrigger');
        const phoneFlag = document.getElementById(options.phoneFlagId || 'phoneCountryFlag');
        const phoneLabel = document.getElementById(options.phoneLabelId || 'phoneCountryLabel');
        const phoneMenu = document.getElementById(options.phoneMenuId || 'phoneCountryMenu');
        const regionRoot = document.getElementById(options.regionRootId || 'regionSelect');
        const regionTrigger = document.getElementById(options.regionTriggerId || 'regionTrigger');
        const regionLabel = document.getElementById(options.regionLabelId || 'regionLabel');
        const regionMenu = document.getElementById(options.regionMenuId || 'regionMenu');
        if (!countryCodeInput || !regionInput || !phoneRoot || !phoneTrigger || !phoneMenu || !regionRoot || !regionTrigger || !regionMenu) {
            return;
        }

        bindGlobalClose();
        const requireCountrySelection = options.requireCountrySelection === true;
        phoneRoot.dataset.selectKind = 'country';
        regionRoot.dataset.selectKind = 'region';

        if (phoneRoot.dataset.bound !== '1') {
            phoneRoot.dataset.bound = '1';
            phoneTrigger.addEventListener('click', (event) => {
                event.stopPropagation();
                toggleSelect(phoneRoot);
            });
            regionTrigger.addEventListener('click', (event) => {
                event.stopPropagation();
                toggleSelect(regionRoot);
            });
        }

        const storedCountry = countryFromStoredRegion(options.preferredRegion || getStored('pepperRegion'));
        const hasStoredCountry = Boolean(options.preferredCountry || getStored('pepperCountry') || options.preferredCountryCode || getStored('pepperPhoneCountryCode'));
        const hasStoredRegion = Boolean(options.preferredRegion || getStored('pepperRegion'));
        let selectedCountry = storedCountry
            || findCountry(options.preferredCountry || getStored('pepperCountry'))
            || findCountry(options.preferredCountryCode || getStored('pepperPhoneCountryCode'))
            || (requireCountrySelection && !hasStoredCountry ? null : DEFAULT_COUNTRY);
        let selectedRegion = selectedCountry
            ? displayRegionFromStored(selectedCountry.name, options.preferredRegion || getStored('pepperRegion'))
            : '';

        function renderPhoneMenu() {
            phoneMenu.innerHTML = '';
            COUNTRY_OPTIONS.forEach((item) => {
                phoneMenu.appendChild(renderCountryOption(item, selectedCountry?.iso, (nextCountry) => {
                    setCountry(nextCountry);
                    toggleSelect(phoneRoot, false);
                }));
            });
        }

        function setRegion(country, region) {
            selectedRegion = region || getRegions(country.name)[0] || country.name;
            if (regionInput) regionInput.value = formatStoredRegion(country.name, selectedRegion);
            if (regionLabel) regionLabel.textContent = selectedRegion;
            regionMenu.innerHTML = '';
            getRegions(country.name).forEach((item) => {
                regionMenu.appendChild(renderRegionOption(country, item, selectedRegion, (nextRegion) => {
                    setRegion(country, nextRegion);
                    toggleSelect(regionRoot, false);
                }));
            });
        }

        function setCountry(country, preferredRegion) {
            if (!country) {
                selectedCountry = null;
                if (countryCodeInput) countryCodeInput.value = '';
                if (countryInput) countryInput.value = '';
                if (countryIsoInput) countryIsoInput.value = '';
                setFlagElement(phoneFlag, null);
                if (phoneLabel) phoneLabel.textContent = options.countryPlaceholder || 'Select country / region';
                if (regionInput) regionInput.value = '';
                if (regionLabel) regionLabel.textContent = options.regionPlaceholder || 'Select region';
                regionMenu.innerHTML = '';
                regionTrigger.disabled = true;
                regionTrigger.setAttribute('aria-disabled', 'true');
                renderPhoneMenu();
                phoneRoot.dispatchEvent(new CustomEvent('countrychange', { detail: null }));
                return;
            }
            selectedCountry = country;
            if (countryCodeInput) countryCodeInput.value = selectedCountry.dial;
            if (countryInput) countryInput.value = selectedCountry.name;
            if (countryIsoInput) countryIsoInput.value = selectedCountry.iso;
            setFlagElement(phoneFlag, selectedCountry);
            if (phoneLabel) phoneLabel.textContent = selectedCountry.dial;
            regionTrigger.disabled = false;
            regionTrigger.setAttribute('aria-disabled', 'false');
            renderPhoneMenu();
            setRegion(selectedCountry, displayRegionFromStored(selectedCountry.name, preferredRegion));
            phoneRoot.dispatchEvent(new CustomEvent('countrychange', { detail: { ...selectedCountry } }));
        }

        setCountry(selectedCountry, options.preferredRegion || getStored('pepperRegion'));

        if (!requireCountrySelection && (!hasStoredCountry || !hasStoredRegion)) {
            getGeoDefaults().then((geo) => {
                if (!geo || geo.status !== 'success') return;
                const geoCountry = countryFrom(geo.country_code || geo.country);
                if (!hasStoredCountry || !hasStoredRegion) {
                    setCountry(geoCountry, hasStoredRegion ? (options.preferredRegion || getStored('pepperRegion')) : '');
                }
            });
        }
    }

    window.MSMEProfileLocation = {
        initPhoneRegionForm,
        initGoogleProfileLocationForm: initPhoneRegionForm,
        COUNTRY_OPTIONS,
        REGIONS_BY_COUNTRY
    };
})();
