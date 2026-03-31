"""
Browser automation to obtain reCAPTCHA token
Using nodriver (undetected-chromedriver successor) to implement anti-detection browser
Supports resident mode: maintaining a globally shared resident tab pool for instant token generation
"""
import asyncio
import inspect
import time
import os
import sys
import re
import json
import shutil
import tempfile
import subprocess
from typing import Optional, Dict, Any, Iterable

from ..core.logger import debug_logger
from ..core.config import config


# ==================== Docker Environment Detection ====================
def _is_running_in_docker() -> bool:
    """Detect if running inside a Docker container"""
    # Method 1: Check for /.dockerenv file
    if os.path.exists('/.dockerenv'):
        return True
    # Method 2: Check cgroup
    try:
        with open('/proc/1/cgroup', 'r') as f:
            content = f.read()
            if 'docker' in content or 'kubepods' in content or 'containerd' in content:
                return True
    except:
        pass
    # Method 3: Check environment variables
    if os.environ.get('DOCKER_CONTAINER') or os.environ.get('KUBERNETES_SERVICE_HOST'):
        return True
    return False


IS_DOCKER = _is_running_in_docker()


def _is_truthy_env(name: str) -> bool:
    """Check if an environment variable is truthy."""
    value = os.environ.get(name, "")
    return value.strip().lower() in {"1", "true", "yes", "on"}


ALLOW_DOCKER_HEADED = (
    _is_truthy_env("ALLOW_DOCKER_HEADED_CAPTCHA")
    or _is_truthy_env("ALLOW_DOCKER_BROWSER_CAPTCHA")
)
DOCKER_HEADED_BLOCKED = IS_DOCKER and not ALLOW_DOCKER_HEADED


# ==================== nodriver Auto-Installation ====================
def _run_pip_install(package: str, use_mirror: bool = False) -> bool:
    """Run pip install command

    Args:
        package: Package name
        use_mirror: Whether to use Chinese mirror

    Returns:
        Whether installation succeeded
    """
    cmd = [sys.executable, '-m', 'pip', 'install', package]
    if use_mirror:
        cmd.extend(['-i', 'https://pypi.tuna.tsinghua.edu.cn/simple'])
    
    try:
        debug_logger.log_info(f"[BrowserCaptcha] Installing {package}...")
        print(f"[BrowserCaptcha] Installing {package}...")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode == 0:
            debug_logger.log_info(f"[BrowserCaptcha] ✅ {package} installed successfully")
            print(f"[BrowserCaptcha] ✅ {package} installed successfully")
            return True
        else:
            debug_logger.log_warning(f"[BrowserCaptcha] {package} installation failed: {result.stderr[:200]}")
            return False
    except Exception as e:
        debug_logger.log_warning(f"[BrowserCaptcha] {package} installation error: {e}")
        return False


def _ensure_nodriver_installed() -> bool:
    """Ensure nodriver is installed

    Returns:
        Whether installation succeeded/is already installed
    """
    try:
        import nodriver
        debug_logger.log_info("[BrowserCaptcha] nodriver is already installed")
        return True
    except ImportError:
        pass
    
    debug_logger.log_info("[BrowserCaptcha] nodriver not installed, starting automatic installation...")
    print("[BrowserCaptcha] nodriver not installed, starting automatic installation...")

    # Try official source first
    if _run_pip_install('nodriver', use_mirror=False):
        return True

    # Official source failed, try Chinese mirror
    debug_logger.log_info("[BrowserCaptcha] Official source installation failed, trying Chinese mirror...")
    print("[BrowserCaptcha] Official source installation failed, trying Chinese mirror...")
    if _run_pip_install('nodriver', use_mirror=True):
        return True

    debug_logger.log_error("[BrowserCaptcha] ❌ nodriver automatic installation failed, please install manually: pip install nodriver")
    print("[BrowserCaptcha] ❌ nodriver automatic installation failed, please install manually: pip install nodriver")
    return False


# Attempt to import nodriver
uc = None
NODRIVER_AVAILABLE = False

if DOCKER_HEADED_BLOCKED:
    debug_logger.log_warning(
        "[BrowserCaptcha] Docker environment detected, built-in browser solving disabled by default."
        "To enable, set ALLOW_DOCKER_HEADED_CAPTCHA=true and provide DISPLAY/Xvfb."
    )
    print("[BrowserCaptcha] ⚠️ Docker environment detected, built-in browser solving disabled by default")
    print("[BrowserCaptcha] To enable, set ALLOW_DOCKER_HEADED_CAPTCHA=true and provide DISPLAY/Xvfb")
else:
    if IS_DOCKER and ALLOW_DOCKER_HEADED:
        debug_logger.log_warning(
            "[BrowserCaptcha] Docker built-in browser solving whitelist enabled, please ensure DISPLAY/Xvfb is available"
        )
        print("[BrowserCaptcha] ✅ Docker built-in browser solving whitelist enabled")
    if _ensure_nodriver_installed():
        try:
            import nodriver as uc
            NODRIVER_AVAILABLE = True
        except ImportError as e:
            debug_logger.log_error(f"[BrowserCaptcha] nodriver import failed: {e}")
            print(f"[BrowserCaptcha] ❌ nodriver import failed: {e}")


def _parse_proxy_url(proxy_url: str):
    """Parse a proxy URL into (protocol, host, port, username, password)."""
    if not proxy_url:
        return None, None, None, None, None
    url = proxy_url.strip()
    if not re.match(r'^(http|https|socks5h?|socks5)://', url):
        url = f"http://{url}"
    m = re.match(r'^(socks5h?|socks5|http|https)://(?:([^:]+):([^@]+)@)?([^:]+):(\d+)$', url)
    if not m:
        return None, None, None, None, None
    protocol, username, password, host, port = m.groups()
    if protocol == "socks5h":
        protocol = "socks5"
    return protocol, host, port, username, password


def _create_proxy_auth_extension(protocol: str, host: str, port: str, username: str, password: str) -> str:
    """Create a temporary Chrome extension directory for proxy authentication.
    Returns the path to the extension directory."""
    ext_dir = tempfile.mkdtemp(prefix="nodriver_proxy_auth_")

    scheme_map = {"http": "http", "https": "https", "socks5": "socks5"}
    scheme = scheme_map.get(protocol, "http")

    manifest = {
        "version": "1.0.0",
        "manifest_version": 2,
        "name": "Proxy Auth Helper",
        "permissions": [
            "proxy", "tabs", "unlimitedStorage", "storage",
            "<all_urls>", "webRequest", "webRequestBlocking"
        ],
        "background": {"scripts": ["background.js"]},
        "minimum_chrome_version": "76.0.0"
    }
    background_js = (
        "var config = {\n"
        '    mode: "fixed_servers",\n'
        "    rules: {\n"
        "        singleProxy: {\n"
        f'            scheme: "{scheme}",\n'
        f'            host: "{host}",\n'
        f"            port: parseInt({port})\n"
        "        },\n"
        '        bypassList: ["localhost"]\n'
        "    }\n"
        "};\n"
        'chrome.proxy.settings.set({value: config, scope: "regular"}, function(){});\n'
        "chrome.webRequest.onAuthRequired.addListener(\n"
        "    function(details) {\n"
        "        return {\n"
        "            authCredentials: {\n"
        f'                username: "{username}",\n'
        f'                password: "{password}"\n'
        "            }\n"
        "        };\n"
        "    },\n"
        '    {urls: ["<all_urls>"]},\n'
        "    ['blocking']\n"
        ");\n"
    )
    with open(os.path.join(ext_dir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    with open(os.path.join(ext_dir, "background.js"), "w", encoding="utf-8") as f:
        f.write(background_js)
    return ext_dir


class ResidentTabInfo:
    """Resident tab information structure"""
    def __init__(self, tab, slot_id: str, project_id: Optional[str] = None):
        self.tab = tab
        self.slot_id = slot_id
        self.project_id = project_id or slot_id
        self.recaptcha_ready = False
        self.created_at = time.time()
        self.last_used_at = time.time()  # Last used time
        self.use_count = 0  # Usage count
        self.solve_lock = asyncio.Lock()  # Serialize execution on the same tab to reduce concurrency conflicts


class BrowserCaptchaService:
    """Browser automation to obtain reCAPTCHA token (nodriver headed mode)

    Supports two modes:
    1. Resident Mode: Maintains a globally shared resident tab pool, whoever gets an idle tab executes
    2. Legacy Mode: Create a new tab for each request (fallback)
    """

    _instance: Optional['BrowserCaptchaService'] = None
    _lock = asyncio.Lock()

    def __init__(self, db=None):
        """Initialize service"""
        self.headless = False  # nodriver headed mode
        self.browser = None
        self._initialized = False
        self.website_key = "6LdsFiUsAAAAAIjVDZcuLhaHiDn5nnHVXVRQGeMV"
        self.db = db
        # Use None to let nodriver automatically create temp directory to avoid directory locking issues
        self.user_data_dir = None

        # Resident mode related attributes: solving tabs are global shared pool, no longer 1:1 bound by project_id
        self._resident_tabs: dict[str, 'ResidentTabInfo'] = {}  # slot_id -> resident tab info
        self._project_resident_affinity: dict[str, str] = {}  # project_id -> slot_id (most recent use)
        self._resident_slot_seq = 0
        self._resident_pick_index = 0
        self._resident_lock = asyncio.Lock()  # Protect resident tab operations
        self._browser_lock = asyncio.Lock()  # Protect browser init/close/restart to avoid duplicate instances
        self._tab_build_lock = asyncio.Lock()  # Serialize cold start/rebuild to reduce nodriver jitter
        self._legacy_lock = asyncio.Lock()  # Avoid legacy fallback concurrent uncontrolled creation of temp tabs
        self._max_resident_tabs = 5  # Max resident tab count (for concurrency)
        self._idle_tab_ttl_seconds = 600  # Tab idle timeout (seconds)
        self._idle_reaper_task: Optional[asyncio.Task] = None  # Idle reaper task
        self._command_timeout_seconds = 8.0
        self._navigation_timeout_seconds = 20.0
        self._solve_timeout_seconds = 45.0
        self._session_refresh_timeout_seconds = 45.0

        # Compatible with old API (keeping single resident attributes as aliases)
        self.resident_project_id: Optional[str] = None  # Backward compatibility
        self.resident_tab = None                         # Backward compatibility
        self._running = False                            # Backward compatibility
        self._recaptcha_ready = False                    # Backward compatibility
        self._last_fingerprint: Optional[Dict[str, Any]] = None
        self._resident_error_streaks: dict[str, int] = {}
        self._proxy_url: Optional[str] = None
        self._proxy_ext_dir: Optional[str] = None
        # Custom site solving resident page (for score-test)
        self._custom_tabs: dict[str, Dict[str, Any]] = {}
        self._custom_lock = asyncio.Lock()

    @classmethod
    async def get_instance(cls, db=None) -> 'BrowserCaptchaService':
        """Get singleton instance"""
        if cls._instance is None:
            async with cls._lock:
                if cls._instance is None:
                    cls._instance = cls(db)
                    # Start idle tab reaper task
                    cls._instance._idle_reaper_task = asyncio.create_task(
                        cls._instance._idle_tab_reaper_loop()
                    )
        return cls._instance

    async def reload_config(self):
        """Hot-reload configuration (reload from database)"""
        from ..core.config import config
        old_max_tabs = self._max_resident_tabs
        old_idle_ttl = self._idle_tab_ttl_seconds

        self._max_resident_tabs = config.personal_max_resident_tabs
        self._idle_tab_ttl_seconds = config.personal_idle_tab_ttl_seconds

        debug_logger.log_info(
            f"[BrowserCaptcha] Personal config hot-updated: "
            f"max_tabs {old_max_tabs}->{self._max_resident_tabs}, "
            f"idle_ttl {old_idle_ttl}s->{self._idle_tab_ttl_seconds}s"
        )

    def _check_available(self):
        """Check if service is available"""
        if DOCKER_HEADED_BLOCKED:
            raise RuntimeError(
                "Docker environment detected, built-in browser solving disabled by default."
                "To enable, set environment variable ALLOW_DOCKER_HEADED_CAPTCHA=true and provide DISPLAY/Xvfb."
            )
        if IS_DOCKER and not os.environ.get("DISPLAY"):
            raise RuntimeError(
                "Docker built-in browser solving enabled, but DISPLAY is not set."
                "Please set DISPLAY (e.g. :99) and start Xvfb."
            )
        if not NODRIVER_AVAILABLE or uc is None:
            raise RuntimeError(
                "nodriver is not installed or unavailable."
                "Please install manually: pip install nodriver"
            )

    async def _run_with_timeout(self, awaitable, timeout_seconds: float, label: str):
        """Unified handling of nodriver operation timeouts to avoid single point of failure blocking the entire request chain."""
        effective_timeout = max(0.5, float(timeout_seconds or 0))
        try:
            return await asyncio.wait_for(awaitable, timeout=effective_timeout)
        except asyncio.TimeoutError as e:
            raise TimeoutError(f"{label} timeout ({effective_timeout:.1f}s)") from e

    async def _tab_evaluate(self, tab, script: str, label: str, timeout_seconds: Optional[float] = None):
        return await self._run_with_timeout(
            tab.evaluate(script),
            timeout_seconds or self._command_timeout_seconds,
            label,
        )

    async def _tab_get(self, tab, url: str, label: str, timeout_seconds: Optional[float] = None):
        return await self._run_with_timeout(
            tab.get(url),
            timeout_seconds or self._navigation_timeout_seconds,
            label,
        )

    async def _browser_get(self, url: str, label: str, new_tab: bool = False, timeout_seconds: Optional[float] = None):
        return await self._run_with_timeout(
            self.browser.get(url, new_tab=new_tab),
            timeout_seconds or self._navigation_timeout_seconds,
            label,
        )

    async def _tab_reload(self, tab, label: str, timeout_seconds: Optional[float] = None):
        return await self._run_with_timeout(
            tab.reload(),
            timeout_seconds or self._navigation_timeout_seconds,
            label,
        )

    async def _get_browser_cookies(self, label: str, timeout_seconds: Optional[float] = None):
        return await self._run_with_timeout(
            self.browser.cookies.get_all(),
            timeout_seconds or self._command_timeout_seconds,
            label,
        )

    async def _browser_send_command(
        self,
        method: str,
        params: Optional[Dict[str, Any]] = None,
        label: Optional[str] = None,
        timeout_seconds: Optional[float] = None,
    ):
        return await self._run_with_timeout(
            self.browser.connection.send(method, params) if params else self.browser.connection.send(method),
            timeout_seconds or self._command_timeout_seconds,
            label or method,
        )

    async def _idle_tab_reaper_loop(self):
        """Idle tab reaper loop"""
        while True:
            try:
                await asyncio.sleep(30)  # Check every 30 seconds
                current_time = time.time()
                tabs_to_close = []

                async with self._resident_lock:
                    for slot_id, resident_info in list(self._resident_tabs.items()):
                        if resident_info.solve_lock.locked():
                            continue
                        idle_seconds = current_time - resident_info.last_used_at
                        if idle_seconds >= self._idle_tab_ttl_seconds:
                            tabs_to_close.append(slot_id)
                            debug_logger.log_info(
                                f"[BrowserCaptcha] slot={slot_id} idle {idle_seconds:.0f}s, preparing to reclaim"
                            )

                for slot_id in tabs_to_close:
                    await self._close_resident_tab(slot_id)

            except asyncio.CancelledError:
                return
            except Exception as e:
                debug_logger.log_warning(f"[BrowserCaptcha] Idle tab reaper exception: {e}")

    async def _evict_lru_tab_if_needed(self) -> bool:
        """If the shared pool limit is reached, use LRU strategy to evict the least recently used idle tab."""
        async with self._resident_lock:
            if len(self._resident_tabs) < self._max_resident_tabs:
                return True

            lru_slot_id = None
            lru_project_hint = None
            lru_last_used = float('inf')

            for slot_id, resident_info in self._resident_tabs.items():
                if resident_info.solve_lock.locked():
                    continue
                if resident_info.last_used_at < lru_last_used:
                    lru_last_used = resident_info.last_used_at
                    lru_slot_id = slot_id
                    lru_project_hint = resident_info.project_id

        if lru_slot_id:
            debug_logger.log_info(
                f"[BrowserCaptcha] Tab count reached limit({self._max_resident_tabs}),"
                f"evicting least recently used slot={lru_slot_id}, project_hint={lru_project_hint}"
            )
            await self._close_resident_tab(lru_slot_id)
            return True

        debug_logger.log_warning(
            f"[BrowserCaptcha] Tab count reached limit({self._max_resident_tabs}),"
            "but there is no safely evictable idle tab currently"
        )
        return False

    async def _get_reserved_tab_ids(self) -> set[int]:
        """Collect tabs currently occupied by resident/custom pool, legacy mode must not reuse them."""
        reserved_tab_ids: set[int] = set()

        async with self._resident_lock:
            for resident_info in self._resident_tabs.values():
                if resident_info and resident_info.tab:
                    reserved_tab_ids.add(id(resident_info.tab))

        async with self._custom_lock:
            for item in self._custom_tabs.values():
                tab = item.get("tab") if isinstance(item, dict) else None
                if tab:
                    reserved_tab_ids.add(id(tab))

        return reserved_tab_ids

    def _next_resident_slot_id(self) -> str:
        self._resident_slot_seq += 1
        return f"slot-{self._resident_slot_seq}"

    def _forget_project_affinity_for_slot_locked(self, slot_id: Optional[str]):
        if not slot_id:
            return
        stale_projects = [
            project_id
            for project_id, mapped_slot_id in self._project_resident_affinity.items()
            if mapped_slot_id == slot_id
        ]
        for project_id in stale_projects:
            self._project_resident_affinity.pop(project_id, None)

    def _resolve_affinity_slot_locked(self, project_id: Optional[str]) -> Optional[str]:
        normalized_project_id = str(project_id or "").strip()
        if not normalized_project_id:
            return None
        slot_id = self._project_resident_affinity.get(normalized_project_id)
        if slot_id and slot_id in self._resident_tabs:
            return slot_id
        if slot_id:
            self._project_resident_affinity.pop(normalized_project_id, None)
        return None

    def _remember_project_affinity(self, project_id: Optional[str], slot_id: Optional[str], resident_info: Optional[ResidentTabInfo]):
        normalized_project_id = str(project_id or "").strip()
        if not normalized_project_id or not slot_id or resident_info is None:
            return
        self._project_resident_affinity[normalized_project_id] = slot_id
        resident_info.project_id = normalized_project_id

    def _resolve_resident_slot_for_project_locked(
        self,
        project_id: Optional[str] = None,
    ) -> tuple[Optional[str], Optional[ResidentTabInfo]]:
        """Prefer recent mapping; fallback to global selection from shared pool when no mapping exists."""
        slot_id = self._resolve_affinity_slot_locked(project_id)
        if slot_id:
            resident_info = self._resident_tabs.get(slot_id)
            if resident_info and resident_info.tab:
                return slot_id, resident_info
        return self._select_resident_slot_locked(project_id)

    def _select_resident_slot_locked(
        self,
        project_id: Optional[str] = None,
    ) -> tuple[Optional[str], Optional[ResidentTabInfo]]:
        candidates = [
            (slot_id, resident_info)
            for slot_id, resident_info in self._resident_tabs.items()
            if resident_info and resident_info.tab
        ]
        if not candidates:
            return None, None

        # Shared solving pool is no longer bound by project_id; here we only do global selection based on
        # "is ready / is idle / usage history" to avoid binding requests to fixed tabs when 4 tokens/4 projects.
        ready_idle = [
            (slot_id, resident_info)
            for slot_id, resident_info in candidates
            if resident_info.recaptcha_ready and not resident_info.solve_lock.locked()
        ]
        ready_busy = [
            (slot_id, resident_info)
            for slot_id, resident_info in candidates
            if resident_info.recaptcha_ready and resident_info.solve_lock.locked()
        ]
        cold_idle = [
            (slot_id, resident_info)
            for slot_id, resident_info in candidates
            if not resident_info.recaptcha_ready and not resident_info.solve_lock.locked()
        ]

        pool = ready_idle or ready_busy or cold_idle or candidates
        pool.sort(key=lambda item: (item[1].last_used_at, item[1].use_count, item[1].created_at, item[0]))

        pick_index = self._resident_pick_index % len(pool)
        self._resident_pick_index = (self._resident_pick_index + 1) % max(len(candidates), 1)
        return pool[pick_index]

    async def _ensure_resident_tab(
        self,
        project_id: Optional[str] = None,
        *,
        force_create: bool = False,
        return_slot_key: bool = False,
    ):
        """Ensure there is an available tab in the shared solving tab pool.

        Logic:
        - Prefer reusing idle tabs
        - If all tabs are busy and limit not reached, continue scaling up
        - After reaching limit, allow requests to queue waiting for existing tabs
        """
        def wrap(slot_id: Optional[str], resident_info: Optional[ResidentTabInfo]):
            if return_slot_key:
                return slot_id, resident_info
            return resident_info

        async with self._resident_lock:
            slot_id, resident_info = self._select_resident_slot_locked(project_id)
            if self._resident_tabs:
                all_busy = all(info.solve_lock.locked() for info in self._resident_tabs.values())
            else:
                all_busy = True

            should_create = force_create or not resident_info or (all_busy and len(self._resident_tabs) < self._max_resident_tabs)
            if not should_create:
                return wrap(slot_id, resident_info)

            if len(self._resident_tabs) >= self._max_resident_tabs:
                return wrap(slot_id, resident_info)

        async with self._tab_build_lock:
            async with self._resident_lock:
                slot_id, resident_info = self._select_resident_slot_locked(project_id)
                if self._resident_tabs:
                    all_busy = all(info.solve_lock.locked() for info in self._resident_tabs.values())
                else:
                    all_busy = True

                should_create = force_create or not resident_info or (all_busy and len(self._resident_tabs) < self._max_resident_tabs)
                if not should_create:
                    return wrap(slot_id, resident_info)

                if len(self._resident_tabs) >= self._max_resident_tabs:
                    return wrap(slot_id, resident_info)

                new_slot_id = self._next_resident_slot_id()

            resident_info = await self._create_resident_tab(new_slot_id, project_id=project_id)
            if resident_info is None:
                async with self._resident_lock:
                    slot_id, fallback_info = self._select_resident_slot_locked(project_id)
                return wrap(slot_id, fallback_info)

            async with self._resident_lock:
                self._resident_tabs[new_slot_id] = resident_info
                self._sync_compat_resident_state()
                return wrap(new_slot_id, resident_info)

    async def _rebuild_resident_tab(
        self,
        project_id: Optional[str] = None,
        *,
        slot_id: Optional[str] = None,
        return_slot_key: bool = False,
    ):
        """Rebuild one tab in the shared pool. Prefer rebuilding the most recently used slot for the current project."""
        def wrap(actual_slot_id: Optional[str], resident_info: Optional[ResidentTabInfo]):
            if return_slot_key:
                return actual_slot_id, resident_info
            return resident_info

        async with self._tab_build_lock:
            async with self._resident_lock:
                actual_slot_id = slot_id
                if actual_slot_id is None:
                    actual_slot_id, _ = self._resolve_resident_slot_for_project_locked(project_id)

                old_resident = self._resident_tabs.pop(actual_slot_id, None) if actual_slot_id else None
                self._forget_project_affinity_for_slot_locked(actual_slot_id)
                if actual_slot_id:
                    self._resident_error_streaks.pop(actual_slot_id, None)
                self._sync_compat_resident_state()

            if old_resident:
                try:
                    async with old_resident.solve_lock:
                        await self._close_tab_quietly(old_resident.tab)
                except Exception:
                    await self._close_tab_quietly(old_resident.tab)

            actual_slot_id = actual_slot_id or self._next_resident_slot_id()
            resident_info = await self._create_resident_tab(actual_slot_id, project_id=project_id)
            if resident_info is None:
                debug_logger.log_warning(
                    f"[BrowserCaptcha] slot={actual_slot_id}, project_id={project_id} Failed to rebuild shared tab"
                )
                return wrap(actual_slot_id, None)

            async with self._resident_lock:
                self._resident_tabs[actual_slot_id] = resident_info
                self._remember_project_affinity(project_id, actual_slot_id, resident_info)
                self._sync_compat_resident_state()
                return wrap(actual_slot_id, resident_info)

    def _sync_compat_resident_state(self):
        """Sync old single resident compatibility attributes."""
        first_resident = next(iter(self._resident_tabs.values()), None)
        if first_resident:
            self.resident_project_id = first_resident.project_id
            self.resident_tab = first_resident.tab
            self._running = True
            self._recaptcha_ready = bool(first_resident.recaptcha_ready)
        else:
            self.resident_project_id = None
            self.resident_tab = None
            self._running = False
            self._recaptcha_ready = False

    async def _close_tab_quietly(self, tab):
        if not tab:
            return
        try:
            await self._run_with_timeout(
                tab.close(),
                timeout_seconds=5.0,
                label="tab.close",
            )
        except Exception:
            pass

    async def _stop_browser_process(self, browser_instance):
        """Compatible with nodriver sync stop API, safely stop browser process."""
        if not browser_instance:
            return
        stop_method = getattr(browser_instance, "stop", None)
        if stop_method is None:
            return
        result = stop_method()
        if inspect.isawaitable(result):
            await self._run_with_timeout(
                result,
                timeout_seconds=10.0,
                label="browser.stop",
            )

    async def _shutdown_browser_runtime_locked(self, reason: str):
        """While holding _browser_lock, thoroughly clean up current browser runtime state."""
        browser_instance = self.browser
        self.browser = None
        self._initialized = False
        self._last_fingerprint = None
        self._cleanup_proxy_extension()
        self._proxy_url = None

        async with self._resident_lock:
            resident_items = list(self._resident_tabs.values())
            self._resident_tabs.clear()
            self._project_resident_affinity.clear()
            self._resident_error_streaks.clear()
            self._sync_compat_resident_state()

        custom_items = list(self._custom_tabs.values())
        self._custom_tabs.clear()

        closed_tabs = set()

        async def close_once(tab):
            if not tab:
                return
            tab_key = id(tab)
            if tab_key in closed_tabs:
                return
            closed_tabs.add(tab_key)
            await self._close_tab_quietly(tab)

        for resident_info in resident_items:
            await close_once(resident_info.tab)

        for item in custom_items:
            tab = item.get("tab") if isinstance(item, dict) else None
            await close_once(tab)

        if browser_instance:
            try:
                await self._stop_browser_process(browser_instance)
            except Exception as e:
                debug_logger.log_warning(
                    f"[BrowserCaptcha] Failed to stop browser instance ({reason}): {e}"
                )

    async def _resolve_personal_proxy(self):
        """Read proxy config for personal captcha browser.
        Priority: captcha browser_proxy > request proxy."""
        if not self.db:
            return None, None, None, None, None
        try:
            captcha_cfg = await self.db.get_captcha_config()
            if captcha_cfg.browser_proxy_enabled and captcha_cfg.browser_proxy_url:
                url = captcha_cfg.browser_proxy_url.strip()
                if url:
                    debug_logger.log_info(f"[BrowserCaptcha] Personal using captcha proxy: {url}")
                    return _parse_proxy_url(url)
        except Exception as e:
            debug_logger.log_warning(f"[BrowserCaptcha] Failed to read captcha proxy configuration: {e}")
        try:
            proxy_cfg = await self.db.get_proxy_config()
            if proxy_cfg and proxy_cfg.enabled and proxy_cfg.proxy_url:
                url = proxy_cfg.proxy_url.strip()
                if url:
                    debug_logger.log_info(f"[BrowserCaptcha] Personal falling back to request proxy: {url}")
                    return _parse_proxy_url(url)
        except Exception as e:
            debug_logger.log_warning(f"[BrowserCaptcha] Failed to read request proxy configuration: {e}")
        return None, None, None, None, None

    def _cleanup_proxy_extension(self):
        """Remove temporary proxy auth extension directory."""
        if self._proxy_ext_dir and os.path.isdir(self._proxy_ext_dir):
            try:
                shutil.rmtree(self._proxy_ext_dir, ignore_errors=True)
            except Exception:
                pass
            self._proxy_ext_dir = None

    async def initialize(self):
        """Initialize nodriver browser"""
        self._check_available()

        async with self._browser_lock:
            browser_needs_restart = False
            browser_executable_path = None
            display_value = os.environ.get("DISPLAY", "").strip()
            browser_args = []

            if self._initialized and self.browser:
                try:
                    if self.browser.stopped:
                        debug_logger.log_warning("[BrowserCaptcha] Browser has stopped, preparing to reinitialize...")
                        browser_needs_restart = True
                    else:
                        if self._idle_reaper_task is None or self._idle_reaper_task.done():
                            self._idle_reaper_task = asyncio.create_task(self._idle_tab_reaper_loop())
                        return
                except Exception as e:
                    debug_logger.log_warning(f"[BrowserCaptcha] Browser status check exception, preparing to reinitialize: {e}")
                    browser_needs_restart = True
            elif self.browser is not None or self._initialized:
                browser_needs_restart = True

            if browser_needs_restart:
                await self._shutdown_browser_runtime_locked(reason="initialize_recovery")

            try:
                if self.user_data_dir:
                    debug_logger.log_info(f"[BrowserCaptcha] Starting nodriver browser (user data directory: {self.user_data_dir})...")
                    os.makedirs(self.user_data_dir, exist_ok=True)
                else:
                    debug_logger.log_info(f"[BrowserCaptcha] Starting nodriver browser (using temp directory)...")

                browser_executable_path = os.environ.get("BROWSER_EXECUTABLE_PATH", "").strip() or None
                if browser_executable_path and not os.path.exists(browser_executable_path):
                    debug_logger.log_warning(
                        f"[BrowserCaptcha] Specified browser does not exist, switching to auto-discovery: {browser_executable_path}"
                    )
                    browser_executable_path = None
                if browser_executable_path:
                    debug_logger.log_info(
                        f"[BrowserCaptcha] Using specified browser executable: {browser_executable_path}"
                    )
                    try:
                        version_result = subprocess.run(
                            [browser_executable_path, "--version"],
                            capture_output=True,
                            text=True,
                            timeout=10,
                        )
                        version_output = (
                            (version_result.stdout or "").strip()
                            or (version_result.stderr or "").strip()
                            or "<empty>"
                        )
                        debug_logger.log_info(
                            "[BrowserCaptcha] Browser version detection: "
                            f"rc={version_result.returncode}, output={version_output[:200]}"
                        )
                    except Exception as version_error:
                        debug_logger.log_warning(
                            f"[BrowserCaptcha] Browser version detection failed: {version_error}"
                        )

                # Parse proxy configuration
                self._cleanup_proxy_extension()
                self._proxy_url = None
                protocol, host, port, username, password = await self._resolve_personal_proxy()
                proxy_server_arg = None
                if protocol and host and port:
                    if username and password:
                        self._proxy_ext_dir = _create_proxy_auth_extension(protocol, host, port, username, password)
                        debug_logger.log_info(
                            f"[BrowserCaptcha] Personal proxy requires authentication, created extension: {self._proxy_ext_dir}"
                        )
                    proxy_server_arg = f"--proxy-server={protocol}://{host}:{port}"
                    self._proxy_url = f"{protocol}://{host}:{port}"
                    debug_logger.log_info(f"[BrowserCaptcha] Personal browser proxy: {self._proxy_url}")

                browser_args = [
                    '--disable-quic',
                    '--disable-features=UseDnsHttpsSvcb',
                    '--disable-dev-shm-usage',
                    '--disable-setuid-sandbox',
                    '--disable-gpu',
                    '--disable-infobars',
                    '--hide-scrollbars',
                    '--window-size=1280,720',
                    '--window-position=3000,3000',
                    '--profile-directory=Default',
                    '--disable-background-networking',
                    '--disable-sync',
                    '--disable-translate',
                    '--disable-default-apps',
                    '--no-first-run',
                    '--no-default-browser-check',
                    '--no-zygote',
                ]
                if proxy_server_arg:
                    browser_args.append(proxy_server_arg)
                if self._proxy_ext_dir:
                    browser_args.append(f'--load-extension={self._proxy_ext_dir}')
                else:
                    browser_args.append('--disable-extensions')

                effective_uid = "n/a"
                if hasattr(os, "geteuid"):
                    try:
                        effective_uid = str(os.geteuid())
                    except Exception:
                        effective_uid = "unknown"
                debug_logger.log_info(
                    "[BrowserCaptcha] nodriver startup context: "
                    f"docker={IS_DOCKER}, display={display_value or '<empty>'}, "
                    f"uid={effective_uid}, headless={self.headless}, sandbox=False, "
                    f"executable={browser_executable_path or '<auto>'}, "
                    f"args={' '.join(browser_args)}"
                )

                # Start nodriver browser (background startup, not occupying foreground)
                config = uc.Config(
                    headless=self.headless,
                    user_data_dir=self.user_data_dir,
                    browser_executable_path=browser_executable_path,
                    sandbox=False,
                    browser_args=browser_args,
                )
                self.browser = await self._run_with_timeout(
                    uc.start(config),
                    timeout_seconds=30.0,
                    label="nodriver.start",
                )

                self._initialized = True
                if self._idle_reaper_task is None or self._idle_reaper_task.done():
                    self._idle_reaper_task = asyncio.create_task(self._idle_tab_reaper_loop())
                debug_logger.log_info(f"[BrowserCaptcha] ✅ nodriver browser started (Profile: {self.user_data_dir})")

            except Exception as e:
                self.browser = None
                self._initialized = False
                debug_logger.log_error(
                    "[BrowserCaptcha] ❌ Browser startup failed: "
                    f"{type(e).__name__}: {str(e)} | "
                    f"display={display_value or '<empty>'} | "
                    f"executable={browser_executable_path or '<auto>'} | "
                    f"args={' '.join(browser_args) if browser_args else '<none>'}"
                )
                raise

    async def warmup_resident_tabs(self, project_ids: Iterable[str], limit: Optional[int] = None) -> list[str]:
        """Warm up shared solving tab pool to reduce cold start jitter for the first request."""
        normalized_project_ids: list[str] = []
        seen_projects = set()
        for raw_project_id in project_ids:
            project_id = str(raw_project_id or "").strip()
            if not project_id or project_id in seen_projects:
                continue
            seen_projects.add(project_id)
            normalized_project_ids.append(project_id)

        await self.initialize()

        try:
            warm_limit = self._max_resident_tabs if limit is None else max(1, min(self._max_resident_tabs, int(limit)))
        except Exception:
            warm_limit = self._max_resident_tabs

        warmed_slots: list[str] = []
        for index in range(warm_limit):
            warm_project_id = normalized_project_ids[index] if index < len(normalized_project_ids) else f"warmup-{index + 1}"
            slot_id, resident_info = await self._ensure_resident_tab(
                warm_project_id,
                force_create=True,
                return_slot_key=True,
            )
            if resident_info and resident_info.tab and slot_id:
                if slot_id not in warmed_slots:
                    warmed_slots.append(slot_id)
                continue
            debug_logger.log_warning(f"[BrowserCaptcha] Failed to warm up shared tab (seed={warm_project_id})")

        return warmed_slots

    # ========== Resident Mode API ==========

    async def start_resident_mode(self, project_id: str):
        """Start resident mode

        Args:
            project_id: Project ID for resident mode
        """
        if not str(project_id or "").strip():
            debug_logger.log_warning("[BrowserCaptcha] Failed to start resident mode: project_id is empty")
            return

        warmed_slots = await self.warmup_resident_tabs([project_id], limit=1)
        if warmed_slots:
            debug_logger.log_info(f"[BrowserCaptcha] ✅ Shared resident solving pool started (seed_project: {project_id})")
            return

        debug_logger.log_error(f"[BrowserCaptcha] Resident mode startup failed (seed_project: {project_id})")

    async def stop_resident_mode(self, project_id: Optional[str] = None):
        """Stop resident mode

        Args:
            project_id: Specify project_id or slot_id; if None, close all resident tabs
        """
        target_slot_id = None
        if project_id:
            async with self._resident_lock:
                target_slot_id = project_id if project_id in self._resident_tabs else self._resolve_affinity_slot_locked(project_id)

        if target_slot_id:
            await self._close_resident_tab(target_slot_id)
            self._resident_error_streaks.pop(target_slot_id, None)
            debug_logger.log_info(f"[BrowserCaptcha] Closed shared tab slot={target_slot_id} (request={project_id})")
            return

        async with self._resident_lock:
            slot_ids = list(self._resident_tabs.keys())
            resident_items = list(self._resident_tabs.values())
            self._resident_tabs.clear()
            self._project_resident_affinity.clear()
            self._resident_error_streaks.clear()
            self._sync_compat_resident_state()

        for resident_info in resident_items:
            if resident_info and resident_info.tab:
                await self._close_tab_quietly(resident_info.tab)
        debug_logger.log_info(f"[BrowserCaptcha] Closed all shared resident tabs (total {len(slot_ids)} tabs)")

    async def _wait_for_document_ready(self, tab, retries: int = 30, interval_seconds: float = 1.0) -> bool:
        """Wait for page document to load."""
        for _ in range(retries):
            try:
                ready_state = await self._tab_evaluate(
                    tab,
                    "document.readyState",
                    label="document.readyState",
                    timeout_seconds=2.0,
                )
                if ready_state == "complete":
                    return True
            except Exception:
                pass
            await asyncio.sleep(interval_seconds)
        return False

    def _is_server_side_flow_error(self, error_text: str) -> bool:
        error_lower = (error_text or "").lower()
        return any(keyword in error_lower for keyword in [
            "http error 500",
            "public_error",
            "internal error",
            "reason=internal",
            "reason: internal",
            "\"reason\":\"internal\"",
            "server error",
            "upstream error",
        ])

    async def _clear_tab_site_storage(self, tab) -> Dict[str, Any]:
        """Clear local storage state of current site, but keep cookies login state."""
        result = await self._tab_evaluate(tab, """
            (async () => {
                const summary = {
                    local_storage_cleared: false,
                    session_storage_cleared: false,
                    cache_storage_deleted: [],
                    indexed_db_deleted: [],
                    indexed_db_errors: [],
                    service_worker_unregistered: 0,
                };

                try {
                    window.localStorage.clear();
                    summary.local_storage_cleared = true;
                } catch (e) {
                    summary.local_storage_error = String(e);
                }

                try {
                    window.sessionStorage.clear();
                    summary.session_storage_cleared = true;
                } catch (e) {
                    summary.session_storage_error = String(e);
                }

                try {
                    if (typeof caches !== 'undefined') {
                        const cacheKeys = await caches.keys();
                        for (const key of cacheKeys) {
                            const deleted = await caches.delete(key);
                            if (deleted) {
                                summary.cache_storage_deleted.push(key);
                            }
                        }
                    }
                } catch (e) {
                    summary.cache_storage_error = String(e);
                }

                try {
                    if (navigator.serviceWorker) {
                        const registrations = await navigator.serviceWorker.getRegistrations();
                        for (const registration of registrations) {
                            const ok = await registration.unregister();
                            if (ok) {
                                summary.service_worker_unregistered += 1;
                            }
                        }
                    }
                } catch (e) {
                    summary.service_worker_error = String(e);
                }

                try {
                    if (typeof indexedDB !== 'undefined' && typeof indexedDB.databases === 'function') {
                        const dbs = await indexedDB.databases();
                        const names = Array.from(new Set(
                            dbs
                                .map((item) => item && item.name)
                                .filter((name) => typeof name === 'string' && name)
                        ));
                        for (const name of names) {
                            try {
                                await new Promise((resolve) => {
                                    const request = indexedDB.deleteDatabase(name);
                                    request.onsuccess = () => resolve(true);
                                    request.onerror = () => resolve(false);
                                    request.onblocked = () => resolve(false);
                                });
                                summary.indexed_db_deleted.push(name);
                            } catch (e) {
                                summary.indexed_db_errors.push(`${name}: ${String(e)}`);
                            }
                        }
                    } else {
                        summary.indexed_db_unsupported = true;
                    }
                } catch (e) {
                    summary.indexed_db_errors.push(String(e));
                }

                return summary;
            })()
        """, label="clear_tab_site_storage", timeout_seconds=15.0)
        return result if isinstance(result, dict) else {}

    async def _clear_resident_storage_and_reload(self, project_id: str) -> bool:
        """Clear resident tab's site data and reload, try self-healing in place."""
        async with self._resident_lock:
            slot_id, resident_info = self._resolve_resident_slot_for_project_locked(project_id)

        if not resident_info or not resident_info.tab:
            debug_logger.log_warning(f"[BrowserCaptcha] project_id={project_id} No shared tab available to clear")
            return False

        try:
            async with resident_info.solve_lock:
                cleanup_summary = await self._clear_tab_site_storage(resident_info.tab)
                debug_logger.log_warning(
                    f"[BrowserCaptcha] project_id={project_id}, slot={slot_id} Site storage cleared, preparing to reload and recover: {cleanup_summary}"
                )

                resident_info.recaptcha_ready = False
                await self._tab_reload(
                    resident_info.tab,
                    label=f"clear_resident_reload:{slot_id or project_id}",
                )

                if not await self._wait_for_document_ready(resident_info.tab, retries=30, interval_seconds=1.0):
                    debug_logger.log_warning(f"[BrowserCaptcha] project_id={project_id}, slot={slot_id} Page load timeout after cleanup")
                    return False

                resident_info.recaptcha_ready = await self._wait_for_recaptcha(resident_info.tab)
                if resident_info.recaptcha_ready:
                    resident_info.last_used_at = time.time()
                    self._remember_project_affinity(project_id, slot_id, resident_info)
                    self._resident_error_streaks.pop(slot_id, None)
                    debug_logger.log_warning(f"[BrowserCaptcha] project_id={project_id}, slot={slot_id} reCAPTCHA recovered after cleanup")
                    return True

                debug_logger.log_warning(f"[BrowserCaptcha] project_id={project_id}, slot={slot_id} Still cannot recover reCAPTCHA after cleanup")
                return False
        except Exception as e:
            debug_logger.log_warning(f"[BrowserCaptcha] project_id={project_id}, slot={slot_id} Cleanup or reload failed: {e}")
            return False

    async def _recreate_resident_tab(self, project_id: str) -> bool:
        """Close and rebuild resident tab."""
        slot_id, resident_info = await self._rebuild_resident_tab(project_id, return_slot_key=True)
        if resident_info is None:
            debug_logger.log_warning(f"[BrowserCaptcha] project_id={project_id} Failed to rebuild shared tab")
            return False
        debug_logger.log_warning(f"[BrowserCaptcha] project_id={project_id} Rebuilt shared tab slot={slot_id}")
        return True

    async def _restart_browser_for_project(self, project_id: str) -> bool:
        """Restart entire nodriver browser and restore shared solving pool."""
        async with self._resident_lock:
            restore_slots = max(1, min(self._max_resident_tabs, len(self._resident_tabs) or 1))
            restore_project_ids: list[str] = []
            seen_projects = set()
            for candidate in [project_id, *self._project_resident_affinity.keys()]:
                normalized_project_id = str(candidate or "").strip()
                if not normalized_project_id or normalized_project_id in seen_projects:
                    continue
                seen_projects.add(normalized_project_id)
                restore_project_ids.append(normalized_project_id)
                if len(restore_project_ids) >= restore_slots:
                    break

        debug_logger.log_warning(f"[BrowserCaptcha] project_id={project_id} Preparing to restart nodriver browser to recover")
        await self._shutdown_browser_runtime(cancel_idle_reaper=False, reason=f"restart_project:{project_id}")

        warmed_slots = await self.warmup_resident_tabs(restore_project_ids, limit=restore_slots)
        if not warmed_slots:
            debug_logger.log_warning(f"[BrowserCaptcha] project_id={project_id} Failed to recover shared tabs after browser restart")
            return False

        slot_id, resident_info = await self._ensure_resident_tab(project_id, return_slot_key=True)
        if resident_info is None or not slot_id:
            debug_logger.log_warning(f"[BrowserCaptcha] project_id={project_id} Cannot locate available shared tab after browser restart")
            return False

        self._remember_project_affinity(project_id, slot_id, resident_info)
        self._resident_error_streaks.pop(slot_id, None)
        debug_logger.log_warning(
            f"[BrowserCaptcha] project_id={project_id} Shared tab pool recovered after browser restart "
            f"(slots={len(warmed_slots)}, active_slot={slot_id})"
        )
        return True

    async def report_flow_error(self, project_id: str, error_reason: str, error_message: str = ""):
        """Self-healing recovery for resident tabs when upstream generation API has an error."""
        if not project_id:
            return

        async with self._resident_lock:
            slot_id, _ = self._resolve_resident_slot_for_project_locked(project_id)

        if not slot_id:
            return

        streak = self._resident_error_streaks.get(slot_id, 0) + 1
        self._resident_error_streaks[slot_id] = streak
        error_text = f"{error_reason or ''} {error_message or ''}".strip()
        error_lower = error_text.lower()
        debug_logger.log_warning(
            f"[BrowserCaptcha] project_id={project_id}, slot={slot_id} Received upstream error, streak={streak}, reason={error_reason}, detail={error_message[:200]}"
        )

        if not self._initialized or not self.browser:
            return

        # 403 error: clear cache first then rebuild
        if "403" in error_text or "forbidden" in error_lower or "recaptcha" in error_lower:
            debug_logger.log_warning(
                f"[BrowserCaptcha] project_id={project_id} Detected 403/reCAPTCHA error, clearing cache and rebuilding"
            )
            healed = await self._clear_resident_storage_and_reload(project_id)
            if not healed:
                await self._recreate_resident_tab(project_id)
            return

        # Server-side error: decide recovery strategy based on consecutive failure count
        if self._is_server_side_flow_error(error_text):
            recreate_threshold = max(2, int(getattr(config, "browser_personal_recreate_threshold", 2) or 2))
            restart_threshold = max(3, int(getattr(config, "browser_personal_restart_threshold", 3) or 3))

            if streak >= restart_threshold:
                await self._restart_browser_for_project(project_id)
                return
            if streak >= recreate_threshold:
                await self._recreate_resident_tab(project_id)
                return

            healed = await self._clear_resident_storage_and_reload(project_id)
            if not healed:
                await self._recreate_resident_tab(project_id)
            return

        # Other errors: directly rebuild tab
        await self._recreate_resident_tab(project_id)

    async def _wait_for_recaptcha(self, tab) -> bool:
        """Wait for reCAPTCHA to load

        Returns:
            True if reCAPTCHA loaded successfully
        """
        debug_logger.log_info("[BrowserCaptcha] Injecting reCAPTCHA script...")

        # Inject reCAPTCHA Enterprise script
        await self._tab_evaluate(tab, f"""
            (() => {{
                if (document.querySelector('script[src*="recaptcha"]')) return;
                const script = document.createElement('script');
                script.src = 'https://www.google.com/recaptcha/enterprise.js?render={self.website_key}';
                script.async = true;
                document.head.appendChild(script);
            }})()
        """, label="inject_recaptcha_script", timeout_seconds=5.0)

        # Wait for reCAPTCHA to load (reduced wait time)
        for i in range(15):  # Reduced to 15 attempts, max 7.5 seconds
            try:
                is_ready = await self._tab_evaluate(
                    tab,
                    "typeof grecaptcha !== 'undefined' && "
                    "typeof grecaptcha.enterprise !== 'undefined' && "
                    "typeof grecaptcha.enterprise.execute === 'function'",
                    label="check_recaptcha_ready",
                    timeout_seconds=2.5,
                )

                if is_ready:
                    debug_logger.log_info(f"[BrowserCaptcha] reCAPTCHA is ready (waited {i * 0.5}s)")
                    return True

                await tab.sleep(0.5)
            except Exception as e:
                debug_logger.log_warning(f"[BrowserCaptcha] Exception when checking reCAPTCHA: {e}")
                await tab.sleep(0.3)  # Reduce wait time on exception

        debug_logger.log_warning("[BrowserCaptcha] reCAPTCHA load timeout")
        return False

    async def _wait_for_custom_recaptcha(
        self,
        tab,
        website_key: str,
        enterprise: bool = False,
    ) -> bool:
        """Wait for reCAPTCHA to load for any site, used for score testing."""
        debug_logger.log_info("[BrowserCaptcha] Detecting custom reCAPTCHA...")

        ready_check = (
            "typeof grecaptcha !== 'undefined' && typeof grecaptcha.enterprise !== 'undefined' && "
            "typeof grecaptcha.enterprise.execute === 'function'"
        ) if enterprise else (
            "typeof grecaptcha !== 'undefined' && typeof grecaptcha.execute === 'function'"
        )
        script_path = "recaptcha/enterprise.js" if enterprise else "recaptcha/api.js"
        label = "Enterprise" if enterprise else "V3"

        is_ready = await self._tab_evaluate(
            tab,
            ready_check,
            label="check_custom_recaptcha_preloaded",
            timeout_seconds=2.5,
        )
        if is_ready:
            debug_logger.log_info(f"[BrowserCaptcha] Custom reCAPTCHA {label} loaded")
            return True

        debug_logger.log_info("[BrowserCaptcha] Custom reCAPTCHA not detected, injecting script...")
        await self._tab_evaluate(tab, f"""
            (() => {{
                if (document.querySelector('script[src*="recaptcha"]')) return;
                const script = document.createElement('script');
                script.src = 'https://www.google.com/{script_path}?render={website_key}';
                script.async = true;
                document.head.appendChild(script);
            }})()
        """, label="inject_custom_recaptcha_script", timeout_seconds=5.0)

        await tab.sleep(3)
        for i in range(20):
            is_ready = await self._tab_evaluate(
                tab,
                ready_check,
                label="check_custom_recaptcha_ready",
                timeout_seconds=2.5,
            )
            if is_ready:
                debug_logger.log_info(f"[BrowserCaptcha] Custom reCAPTCHA {label} loaded (waited {i * 0.5} seconds)")
                return True
            await tab.sleep(0.5)

        debug_logger.log_warning("[BrowserCaptcha] Custom reCAPTCHA load timeout")
        return False

    async def _execute_recaptcha_on_tab(self, tab, action: str = "IMAGE_GENERATION") -> Optional[str]:
        """Execute reCAPTCHA on the specified tab to get token

        Args:
            tab: nodriver tab object
            action: reCAPTCHA action type (IMAGE_GENERATION or VIDEO_GENERATION)

        Returns:
            reCAPTCHA token or None
        """
        # Generate unique variable names to avoid conflicts
        ts = int(time.time() * 1000)
        token_var = f"_recaptcha_token_{ts}"
        error_var = f"_recaptcha_error_{ts}"

        execute_script = f"""
            (() => {{
                window.{token_var} = null;
                window.{error_var} = null;

                try {{
                    grecaptcha.enterprise.ready(function() {{
                        grecaptcha.enterprise.execute('{self.website_key}', {{action: '{action}'}})
                            .then(function(token) {{
                                window.{token_var} = token;
                            }})
                            .catch(function(err) {{
                                window.{error_var} = err.message || 'execute failed';
                            }});
                    }});
                }} catch (e) {{
                    window.{error_var} = e.message || 'exception';
                }}
            }})()
        """

        # Inject execution script
        await self._tab_evaluate(
            tab,
            execute_script,
            label=f"execute_recaptcha:{action}",
            timeout_seconds=5.0,
        )

        # Poll for results (up to 30 seconds)
        token = None
        for i in range(60):
            await tab.sleep(0.5)
            token = await self._tab_evaluate(
                tab,
                f"window.{token_var}",
                label=f"poll_recaptcha_token:{action}",
                timeout_seconds=2.0,
            )
            if token:
                break
            error = await self._tab_evaluate(
                tab,
                f"window.{error_var}",
                label=f"poll_recaptcha_error:{action}",
                timeout_seconds=2.0,
            )
            if error:
                debug_logger.log_error(f"[BrowserCaptcha] reCAPTCHA error: {error}")
                break

        # Clean up temporary variables
        try:
            await self._tab_evaluate(
                tab,
                f"delete window.{token_var}; delete window.{error_var};",
                label="cleanup_recaptcha_temp_vars",
                timeout_seconds=5.0,
            )
        except:
            pass

        if token:
            debug_logger.log_info(f"[BrowserCaptcha] ✅ Token obtained successfully (length: {len(token)})")
        else:
            debug_logger.log_warning("[BrowserCaptcha] Token acquisition failed, delegating to upper layer for tab recovery")

        return token

    async def _execute_custom_recaptcha_on_tab(
        self,
        tab,
        website_key: str,
        action: str = "homepage",
        enterprise: bool = False,
    ) -> Optional[str]:
        """Execute reCAPTCHA for any site on the specified tab."""
        ts = int(time.time() * 1000)
        token_var = f"_custom_recaptcha_token_{ts}"
        error_var = f"_custom_recaptcha_error_{ts}"
        execute_target = "grecaptcha.enterprise.execute" if enterprise else "grecaptcha.execute"

        execute_script = f"""
            (() => {{
                window.{token_var} = null;
                window.{error_var} = null;

                try {{
                    grecaptcha.ready(function() {{
                        {execute_target}('{website_key}', {{action: '{action}'}})
                            .then(function(token) {{
                                window.{token_var} = token;
                            }})
                            .catch(function(err) {{
                                window.{error_var} = err.message || 'execute failed';
                            }});
                    }});
                }} catch (e) {{
                    window.{error_var} = e.message || 'exception';
                }}
            }})()
        """

        await self._tab_evaluate(
            tab,
            execute_script,
            label=f"execute_custom_recaptcha:{action}",
            timeout_seconds=5.0,
        )

        token = None
        for _ in range(30):
            await tab.sleep(0.5)
            token = await self._tab_evaluate(
                tab,
                f"window.{token_var}",
                label=f"poll_custom_recaptcha_token:{action}",
                timeout_seconds=2.0,
            )
            if token:
                break
            error = await self._tab_evaluate(
                tab,
                f"window.{error_var}",
                label=f"poll_custom_recaptcha_error:{action}",
                timeout_seconds=2.0,
            )
            if error:
                debug_logger.log_error(f"[BrowserCaptcha] Custom reCAPTCHA error: {error}")
                break

        try:
            await self._tab_evaluate(
                tab,
                f"delete window.{token_var}; delete window.{error_var};",
                label="cleanup_custom_recaptcha_temp_vars",
                timeout_seconds=5.0,
            )
        except:
            pass

        if token:
            post_wait_seconds = 3
            try:
                post_wait_seconds = float(getattr(config, "browser_recaptcha_settle_seconds", 3) or 3)
            except Exception:
                pass
            if post_wait_seconds > 0:
                debug_logger.log_info(
                    f"[BrowserCaptcha] Custom reCAPTCHA completed, waiting additional {post_wait_seconds:.1f}s before returning token"
                )
                await tab.sleep(post_wait_seconds)

        return token

    async def _verify_score_on_tab(self, tab, token: str, verify_url: str) -> Dict[str, Any]:
        """Directly read the score displayed on the test page to avoid inconsistency between verify.php and page display."""
        _ = token
        _ = verify_url
        started_at = time.time()
        timeout_seconds = 25.0
        refresh_clicked = False
        last_snapshot: Dict[str, Any] = {}

        try:
            timeout_seconds = float(getattr(config, "browser_score_dom_wait_seconds", 25) or 25)
        except Exception:
            pass

        while (time.time() - started_at) < timeout_seconds:
            try:
                result = await self._tab_evaluate(tab, """
                    (() => {
                        const bodyText = ((document.body && document.body.innerText) || "")
                            .replace(/\\u00a0/g, " ")
                            .replace(/\\r/g, "");
                        const patterns = [
                            { source: "current_score", regex: /Your score is:\\s*([01](?:\\.\\d+)?)/i },
                            { source: "selected_score", regex: /Selected Score Test:[\\s\\S]{0,400}?Score:\\s*([01](?:\\.\\d+)?)/i },
                            { source: "history_score", regex: /(?:^|\\n)\\s*Score:\\s*([01](?:\\.\\d+)?)\\s*;/i },
                        ];
                        let score = null;
                        let source = "";
                        for (const item of patterns) {
                            const match = bodyText.match(item.regex);
                            if (!match) continue;
                            const parsed = Number(match[1]);
                            if (!Number.isNaN(parsed) && parsed >= 0 && parsed <= 1) {
                                score = parsed;
                                source = item.source;
                                break;
                            }
                        }
                        const uaMatch = bodyText.match(/Current User Agent:\\s*([^\\n]+)/i);
                        const ipMatch = bodyText.match(/Current IP Address:\\s*([^\\n]+)/i);
                        return {
                            score,
                            source,
                            raw_text: bodyText.slice(0, 4000),
                            current_user_agent: uaMatch ? uaMatch[1].trim() : "",
                            current_ip_address: ipMatch ? ipMatch[1].trim() : "",
                            title: document.title || "",
                            url: location.href || "",
                        };
                    })()
                """, label="verify_score_dom", timeout_seconds=10.0)
            except Exception as e:
                result = {"error": f"{type(e).__name__}: {str(e)[:200]}"}

            if isinstance(result, dict):
                last_snapshot = result
                score = result.get("score")
                if isinstance(score, (int, float)):
                    elapsed_ms = int((time.time() - started_at) * 1000)
                    return {
                        "verify_mode": "browser_page_dom",
                        "verify_elapsed_ms": elapsed_ms,
                        "verify_http_status": None,
                        "verify_result": {
                            "success": True,
                            "score": score,
                            "source": result.get("source") or "antcpt_dom",
                            "raw_text": result.get("raw_text") or "",
                            "current_user_agent": result.get("current_user_agent") or "",
                            "current_ip_address": result.get("current_ip_address") or "",
                            "page_title": result.get("title") or "",
                            "page_url": result.get("url") or "",
                        },
                    }

            if not refresh_clicked and (time.time() - started_at) >= 2:
                refresh_clicked = True
                try:
                    await self._tab_evaluate(tab, """
                        (() => {
                            const nodes = Array.from(
                                document.querySelectorAll('button, input[type="button"], input[type="submit"], a')
                            );
                            const target = nodes.find((node) => {
                                const text = (node.innerText || node.textContent || node.value || "").trim();
                                return /Refresh score now!?/i.test(text);
                            });
                            if (target) {
                                target.click();
                                return true;
                            }
                            return false;
                        })()
                    """, label="verify_score_click_refresh", timeout_seconds=5.0)
                except Exception:
                    pass

            await tab.sleep(0.5)

        elapsed_ms = int((time.time() - started_at) * 1000)
        if not isinstance(last_snapshot, dict):
            last_snapshot = {"raw": last_snapshot}

        return {
            "verify_mode": "browser_page_dom",
            "verify_elapsed_ms": elapsed_ms,
            "verify_http_status": None,
            "verify_result": {
                "success": False,
                "score": None,
                "source": "antcpt_dom_timeout",
                "raw_text": last_snapshot.get("raw_text") or "",
                "current_user_agent": last_snapshot.get("current_user_agent") or "",
                "current_ip_address": last_snapshot.get("current_ip_address") or "",
                "page_title": last_snapshot.get("title") or "",
                "page_url": last_snapshot.get("url") or "",
                "error": last_snapshot.get("error") or "Failed to read score from page",
            },
        }

    async def _extract_tab_fingerprint(self, tab) -> Optional[Dict[str, Any]]:
        """Extract browser fingerprint information from nodriver tab."""
        try:
            fingerprint = await self._tab_evaluate(tab, """
                () => {
                    const ua = navigator.userAgent || "";
                    const lang = navigator.language || "";
                    const uaData = navigator.userAgentData || null;
                    let secChUa = "";
                    let secChUaMobile = "";
                    let secChUaPlatform = "";

                    if (uaData) {
                        if (Array.isArray(uaData.brands) && uaData.brands.length > 0) {
                            secChUa = uaData.brands
                                .map((item) => `"${item.brand}";v="${item.version}"`)
                                .join(", ");
                        }
                        secChUaMobile = uaData.mobile ? "?1" : "?0";
                        if (uaData.platform) {
                            secChUaPlatform = `"${uaData.platform}"`;
                        }
                    }

                    return {
                        user_agent: ua,
                        accept_language: lang,
                        sec_ch_ua: secChUa,
                        sec_ch_ua_mobile: secChUaMobile,
                        sec_ch_ua_platform: secChUaPlatform,
                    };
                }
            """, label="extract_tab_fingerprint", timeout_seconds=8.0)
            if not isinstance(fingerprint, dict):
                return None

            result: Dict[str, Any] = {"proxy_url": self._proxy_url}
            for key in ("user_agent", "accept_language", "sec_ch_ua", "sec_ch_ua_mobile", "sec_ch_ua_platform"):
                value = fingerprint.get(key)
                if isinstance(value, str) and value:
                    result[key] = value
            return result
        except Exception as e:
            debug_logger.log_warning(f"[BrowserCaptcha] Failed to extract nodriver fingerprint: {e}")
            return None

    # ========== Main API ==========

    async def get_token(self, project_id: str, action: str = "IMAGE_GENERATION") -> Optional[str]:
        """Get reCAPTCHA token

        Uses a global shared solving tab pool. Tabs are no longer bound one-to-one by project_id.
        Whoever gets an idle tab uses it; only Session Token refresh/fault recovery will prioritize the most recent mapping.

        Args:
            project_id: Flow project ID
            action: reCAPTCHA action type
                - IMAGE_GENERATION: Image generation and 2K/4K image upscaling (default)
                - VIDEO_GENERATION: Video generation and video upscaling

        Returns:
            reCAPTCHA token string, or None if acquisition fails
        """
        debug_logger.log_info(f"[BrowserCaptcha] get_token started: project_id={project_id}, action={action}, current tab count={len(self._resident_tabs)}/{self._max_resident_tabs}")

        # Ensure browser is initialized
        await self.initialize()
        self._last_fingerprint = None

        debug_logger.log_info(
            f"[BrowserCaptcha] Starting to acquire tab from shared solving pool (project: {project_id}, current: {len(self._resident_tabs)}/{self._max_resident_tabs})"
        )
        slot_id, resident_info = await self._ensure_resident_tab(project_id, return_slot_key=True)
        if resident_info is None or not slot_id:
            debug_logger.log_warning(
                f"[BrowserCaptcha] Shared tab pool unavailable, falling back to legacy mode (project: {project_id})"
            )
            return await self._get_token_legacy(project_id, action)

        debug_logger.log_info(
            f"[BrowserCaptcha] ✅ Shared tab available (slot={slot_id}, project={project_id}, use_count={resident_info.use_count})"
        )

        if resident_info and resident_info.tab and not resident_info.recaptcha_ready:
            debug_logger.log_warning(
                f"[BrowserCaptcha] Shared tab not ready, preparing to rebuild cold slot={slot_id}, project={project_id}"
            )
            slot_id, resident_info = await self._rebuild_resident_tab(
                project_id,
                slot_id=slot_id,
                return_slot_key=True,
            )

        # Use resident tab to generate token (execute outside lock to avoid blocking)
        if resident_info and resident_info.recaptcha_ready and resident_info.tab:
            start_time = time.time()
            debug_logger.log_info(
                f"[BrowserCaptcha] Generating token from shared resident tab immediately (slot={slot_id}, project={project_id}, action={action})..."
            )
            try:
                async with resident_info.solve_lock:
                    token = await self._run_with_timeout(
                        self._execute_recaptcha_on_tab(resident_info.tab, action),
                        timeout_seconds=self._solve_timeout_seconds,
                        label=f"resident_solve:{slot_id}:{project_id}:{action}",
                    )
                duration_ms = (time.time() - start_time) * 1000
                if token:
                    # Update last used time and count
                    resident_info.last_used_at = time.time()
                    resident_info.use_count += 1
                    self._remember_project_affinity(project_id, slot_id, resident_info)
                    self._resident_error_streaks.pop(slot_id, None)
                    self._last_fingerprint = await self._extract_tab_fingerprint(resident_info.tab)
                    debug_logger.log_info(
                        f"[BrowserCaptcha] ✅ Token generated successfully (slot={slot_id}, duration {duration_ms:.0f}ms, use count: {resident_info.use_count})"
                    )
                    return token
                else:
                    debug_logger.log_warning(
                        f"[BrowserCaptcha] Shared tab generation failed (slot={slot_id}, project={project_id}), attempting rebuild..."
                    )
            except Exception as e:
                debug_logger.log_warning(f"[BrowserCaptcha] Shared tab exception (slot={slot_id}): {e}, attempting rebuild...")

            # Resident tab invalid, attempting rebuild
            debug_logger.log_info(f"[BrowserCaptcha] Starting to rebuild shared tab (slot={slot_id}, project={project_id})")
            slot_id, resident_info = await self._rebuild_resident_tab(
                project_id,
                slot_id=slot_id,
                return_slot_key=True,
            )
            debug_logger.log_info(f"[BrowserCaptcha] Shared tab rebuild completed (slot={slot_id}, project={project_id})")

            # Attempt generation immediately after rebuild (execute outside lock)
            if resident_info:
                try:
                    async with resident_info.solve_lock:
                        token = await self._run_with_timeout(
                            self._execute_recaptcha_on_tab(resident_info.tab, action),
                            timeout_seconds=self._solve_timeout_seconds,
                            label=f"resident_resolve_after_rebuild:{slot_id}:{project_id}:{action}",
                        )
                    if token:
                        resident_info.last_used_at = time.time()
                        resident_info.use_count += 1
                        self._remember_project_affinity(project_id, slot_id, resident_info)
                        self._resident_error_streaks.pop(slot_id, None)
                        self._last_fingerprint = await self._extract_tab_fingerprint(resident_info.tab)
                        debug_logger.log_info(f"[BrowserCaptcha] ✅ Token generated successfully after rebuild (slot={slot_id})")
                        return token
                except Exception:
                    pass

        # Final Fallback: Use legacy mode
        debug_logger.log_warning(f"[BrowserCaptcha] All resident methods failed, falling back to legacy mode (project: {project_id})")
        legacy_token = await self._get_token_legacy(project_id, action)
        if legacy_token:
            if slot_id:
                self._resident_error_streaks.pop(slot_id, None)
        return legacy_token

    async def _create_resident_tab(self, slot_id: str, project_id: Optional[str] = None) -> Optional[ResidentTabInfo]:
        """Create a shared resident solving tab

        Args:
            slot_id: Shared tab slot ID
            project_id: Project ID that triggered creation, used only for logs and recent mapping

        Returns:
            ResidentTabInfo object, or None (creation failed)
        """
        try:
            # Use Flow API address as base page
            website_url = "https://labs.google/fx/api/auth/providers"
            debug_logger.log_info(f"[BrowserCaptcha] Creating shared resident tab slot={slot_id}, seed_project={project_id}")

            async with self._resident_lock:
                existing_tabs = [info.tab for info in self._resident_tabs.values() if info.tab]

            # Get or create tab
            tabs = self.browser.tabs
            available_tab = None

            # Find unoccupied tab
            for tab in tabs:
                if tab not in existing_tabs:
                    available_tab = tab
                    break

            if available_tab:
                tab = available_tab
                debug_logger.log_info(f"[BrowserCaptcha] Reusing unoccupied tab")
                await self._tab_get(
                    tab,
                    website_url,
                    label=f"resident_tab_get:{slot_id}",
                )
            else:
                debug_logger.log_info(f"[BrowserCaptcha] Creating new tab")
                tab = await self._browser_get(
                    website_url,
                    label=f"resident_browser_get:{slot_id}",
                    new_tab=True,
                )

            # Wait for page to load (reduced wait time)
            page_loaded = False
            for retry in range(10):  # Reduced to 10 attempts, max 5 seconds
                try:
                    await asyncio.sleep(0.5)
                    ready_state = await self._tab_evaluate(
                        tab,
                        "document.readyState",
                        label=f"resident_document_ready:{slot_id}",
                        timeout_seconds=2.0,
                    )
                    if ready_state == "complete":
                        page_loaded = True
                        debug_logger.log_info(f"[BrowserCaptcha] Page loaded")
                        break
                except Exception as e:
                    debug_logger.log_warning(f"[BrowserCaptcha] Page wait exception: {e}, retry {retry + 1}/10...")
                    await asyncio.sleep(0.3)  # Reduced retry interval

            if not page_loaded:
                debug_logger.log_error(f"[BrowserCaptcha] Page load timeout (slot={slot_id}, project={project_id})")
                await self._close_tab_quietly(tab)
                return None

            # Wait for reCAPTCHA to load
            recaptcha_ready = await self._wait_for_recaptcha(tab)

            if not recaptcha_ready:
                debug_logger.log_error(f"[BrowserCaptcha] reCAPTCHA load failed (slot={slot_id}, project={project_id})")
                await self._close_tab_quietly(tab)
                return None

            # Create resident info object
            resident_info = ResidentTabInfo(tab, slot_id, project_id=project_id)
            resident_info.recaptcha_ready = True

            debug_logger.log_info(f"[BrowserCaptcha] ✅ Shared resident tab created successfully (slot={slot_id}, project={project_id})")
            return resident_info

        except Exception as e:
            debug_logger.log_error(f"[BrowserCaptcha] Exception creating shared resident tab (slot={slot_id}, project={project_id}): {e}")
            return None

    async def _close_resident_tab(self, slot_id: str):
        """Close the shared resident tab for the specified slot

        Args:
            slot_id: Shared tab slot ID
        """
        async with self._resident_lock:
            resident_info = self._resident_tabs.pop(slot_id, None)
            self._forget_project_affinity_for_slot_locked(slot_id)
            self._resident_error_streaks.pop(slot_id, None)
            self._sync_compat_resident_state()

        if resident_info and resident_info.tab:
            try:
                await self._close_tab_quietly(resident_info.tab)
                debug_logger.log_info(f"[BrowserCaptcha] Closed shared resident tab slot={slot_id}")
            except Exception as e:
                debug_logger.log_warning(f"[BrowserCaptcha] Exception closing tab: {e}")

    async def invalidate_token(self, project_id: str):
        """Called when token invalidation is detected, rebuilds the recently mapped shared tab for the current project.

        Args:
            project_id: Project ID
        """
        debug_logger.log_warning(
            f"[BrowserCaptcha] Token marked as invalid (project: {project_id}), rebuilding only the corresponding tab in shared pool to avoid clearing global browser state"
        )

        # Rebuild tab
        slot_id, resident_info = await self._rebuild_resident_tab(project_id, return_slot_key=True)
        if resident_info and slot_id:
            debug_logger.log_info(f"[BrowserCaptcha] ✅ Tab rebuilt (project: {project_id}, slot={slot_id})")
        else:
            debug_logger.log_error(f"[BrowserCaptcha] Tab rebuild failed (project: {project_id})")

    async def _get_token_legacy(self, project_id: str, action: str = "IMAGE_GENERATION") -> Optional[str]:
        """Legacy mode to get reCAPTCHA token (creates a new tab each time)

        Args:
            project_id: Flow project ID
            action: reCAPTCHA action type (IMAGE_GENERATION or VIDEO_GENERATION)

        Returns:
            reCAPTCHA token string, or None if acquisition fails
        """
        # Ensure browser is started
        if not self._initialized or not self.browser:
            await self.initialize()

        start_time = time.time()
        tab = None

        async with self._legacy_lock:
            try:
                website_url = "https://labs.google/fx/api/auth/providers"
                debug_logger.log_info(
                    f"[BrowserCaptcha] [Legacy] Creating independent temporary tab for verification, to avoid polluting resident/custom pages: {website_url}"
                )
                tab = await self._browser_get(
                    website_url,
                    label=f"legacy_browser_get:{project_id}",
                    new_tab=True,
                )

                # Wait for page to fully load (increased wait time)
                debug_logger.log_info("[BrowserCaptcha] [Legacy] Waiting for page to load...")
                await tab.sleep(3)

                # Wait for page DOM to complete
                for _ in range(10):
                    ready_state = await self._tab_evaluate(
                        tab,
                        "document.readyState",
                        label=f"legacy_document_ready:{project_id}",
                        timeout_seconds=2.0,
                    )
                    if ready_state == "complete":
                        break
                    await tab.sleep(0.5)

                # Wait for reCAPTCHA to load
                recaptcha_ready = await self._wait_for_recaptcha(tab)

                if not recaptcha_ready:
                    debug_logger.log_error("[BrowserCaptcha] [Legacy] reCAPTCHA cannot load")
                    return None

                # Execute reCAPTCHA
                debug_logger.log_info(f"[BrowserCaptcha] [Legacy] Executing reCAPTCHA verification (action: {action})...")
                token = await self._run_with_timeout(
                    self._execute_recaptcha_on_tab(tab, action),
                    timeout_seconds=self._solve_timeout_seconds,
                    label=f"legacy_solve:{project_id}:{action}",
                )

                duration_ms = (time.time() - start_time) * 1000

                if token:
                    self._last_fingerprint = await self._extract_tab_fingerprint(tab)
                    debug_logger.log_info(f"[BrowserCaptcha] [Legacy] ✅ Token obtained successfully (duration {duration_ms:.0f}ms)")
                    return token

                debug_logger.log_error("[BrowserCaptcha] [Legacy] Token acquisition failed (returned null)")
                return None

            except Exception as e:
                debug_logger.log_error(f"[BrowserCaptcha] [Legacy] Token acquisition exception: {str(e)}")
                return None
            finally:
                # Close legacy temporary tab (but keep browser)
                if tab:
                    await self._close_tab_quietly(tab)

    def get_last_fingerprint(self) -> Optional[Dict[str, Any]]:
        """Return the browser fingerprint snapshot from the most recent solving."""
        if not self._last_fingerprint:
            return None
        return dict(self._last_fingerprint)

    async def _clear_browser_cache(self):
        """Clear all browser cache"""
        if not self.browser:
            return

        try:
            debug_logger.log_info("[BrowserCaptcha] Starting to clear browser cache...")

            # Use Chrome DevTools Protocol to clear cache
            # Clear all types of cache data
            await self._browser_send_command(
                "Network.clearBrowserCache",
                label="clear_browser_cache",
            )

            # Clear Cookies
            await self._browser_send_command(
                "Network.clearBrowserCookies",
                label="clear_browser_cookies",
            )

            # Clear storage data (localStorage, sessionStorage, IndexedDB, etc.)
            await self._browser_send_command(
                "Storage.clearDataForOrigin",
                {
                    "origin": "https://www.google.com",
                    "storageTypes": "all"
                },
                label="clear_browser_origin_storage",
            )

            debug_logger.log_info("[BrowserCaptcha] ✅ Browser cache cleared")

        except Exception as e:
            debug_logger.log_warning(f"[BrowserCaptcha] Exception clearing cache: {e}")

    async def _shutdown_browser_runtime(self, cancel_idle_reaper: bool = False, reason: str = "shutdown"):
        if cancel_idle_reaper and self._idle_reaper_task and not self._idle_reaper_task.done():
            self._idle_reaper_task.cancel()
            try:
                await self._idle_reaper_task
            except asyncio.CancelledError:
                pass
            finally:
                self._idle_reaper_task = None

        async with self._browser_lock:
            try:
                await self._shutdown_browser_runtime_locked(reason=reason)
                debug_logger.log_info(f"[BrowserCaptcha] Browser runtime state cleared ({reason})")
            except Exception as e:
                debug_logger.log_error(f"[BrowserCaptcha] Exception clearing browser runtime state ({reason}): {str(e)}")

    async def close(self):
        """Close browser"""
        await self._shutdown_browser_runtime(cancel_idle_reaper=True, reason="service_close")

    async def open_login_window(self):
        """Open login window for user to manually login to Google"""
        await self.initialize()
        tab = await self._browser_get(
            "https://accounts.google.com/",
            label="open_login_window",
            new_tab=True,
        )
        debug_logger.log_info("[BrowserCaptcha] Please login to your account in the opened browser. After logging in, no need to close the browser, the script will automatically use this state next run.")
        print("Please login to your account in the opened browser. After logging in, no need to close the browser, the script will automatically use this state next run.")

    # ========== Session Token Refresh ==========

    async def refresh_session_token(self, project_id: str) -> Optional[str]:
        """Get the latest Session Token from resident tab

        Reuse shared solving tab, refresh page and extract from cookies
        __Secure-next-auth.session-token

        Args:
            project_id: Project ID, used to locate resident tab

        Returns:
            New Session Token, or None if acquisition fails
        """
        # Ensure browser is initialized
        await self.initialize()

        start_time = time.time()
        debug_logger.log_info(f"[BrowserCaptcha] Starting to refresh Session Token (project: {project_id})...")

        async with self._resident_lock:
            slot_id = self._resolve_affinity_slot_locked(project_id)
            resident_info = self._resident_tabs.get(slot_id) if slot_id else None

        if resident_info is None or not slot_id:
            slot_id, resident_info = await self._ensure_resident_tab(project_id, return_slot_key=True)

        if resident_info is None or not slot_id:
            debug_logger.log_warning(f"[BrowserCaptcha] Cannot get shared resident tab for project_id={project_id}")
            return None
        
        if not resident_info or not resident_info.tab:
            debug_logger.log_error(f"[BrowserCaptcha] Cannot get resident tab")
            return None
        
        tab = resident_info.tab
        
        try:
            async with resident_info.solve_lock:
                # Refresh page to get latest cookies
                debug_logger.log_info(f"[BrowserCaptcha] Refreshing resident tab to get latest cookies...")
                resident_info.recaptcha_ready = False
                await self._run_with_timeout(
                    self._tab_reload(
                        tab,
                        label=f"refresh_session_reload:{slot_id}",
                    ),
                    timeout_seconds=self._session_refresh_timeout_seconds,
                    label=f"refresh_session_reload_total:{slot_id}",
                )
                
                # Wait for page to load
                for i in range(30):
                    await asyncio.sleep(1)
                    try:
                        ready_state = await self._tab_evaluate(
                            tab,
                            "document.readyState",
                            label=f"refresh_session_ready_state:{slot_id}",
                            timeout_seconds=2.0,
                        )
                        if ready_state == "complete":
                            break
                    except Exception:
                        pass

                resident_info.recaptcha_ready = await self._wait_for_recaptcha(tab)
                if not resident_info.recaptcha_ready:
                    debug_logger.log_warning(
                        f"[BrowserCaptcha] After refreshing Session Token, reCAPTCHA not ready (slot={slot_id})"
                    )
                
                # Extra wait to ensure cookies are set
                await asyncio.sleep(2)
                
                # Extract __Secure-next-auth.session-token from cookies
                # nodriver can get cookies through browser
                session_token = None
                
                try:
                    # Use nodriver cookies API to get all cookies
                    cookies = await self._get_browser_cookies(
                        label=f"refresh_session_get_cookies:{slot_id}",
                    )
                    
                    for cookie in cookies:
                        if cookie.name == "__Secure-next-auth.session-token":
                            session_token = cookie.value
                            break
                            
                except Exception as e:
                    debug_logger.log_warning(f"[BrowserCaptcha] Cookies API retrieval failed: {e}, trying to get from document.cookie...")
                    
                    # Fallback: Get via JavaScript (note: HttpOnly cookies may not be accessible this way)
                    try:
                        all_cookies = await self._tab_evaluate(
                            tab,
                            "document.cookie",
                            label=f"refresh_session_document_cookie:{slot_id}",
                        )
                        if all_cookies:
                            for part in all_cookies.split(";"):
                                part = part.strip()
                                if part.startswith("__Secure-next-auth.session-token="):
                                    session_token = part.split("=", 1)[1]
                                    break
                    except Exception as e2:
                        debug_logger.log_error(f"[BrowserCaptcha] document.cookie retrieval failed: {e2}")
            
            duration_ms = (time.time() - start_time) * 1000
            
            if session_token:
                resident_info.last_used_at = time.time()
                self._remember_project_affinity(project_id, slot_id, resident_info)
                self._resident_error_streaks.pop(slot_id, None)
                debug_logger.log_info(f"[BrowserCaptcha] ✅ Session Token obtained successfully (duration {duration_ms:.0f}ms)")
                return session_token
            else:
                debug_logger.log_error(f"[BrowserCaptcha] ❌ __Secure-next-auth.session-token cookie not found")
                return None
                
        except Exception as e:
            debug_logger.log_error(f"[BrowserCaptcha] Session Token refresh exception: {str(e)}")
            
            # Shared tab may be invalid, attempting rebuild
            slot_id, resident_info = await self._rebuild_resident_tab(project_id, slot_id=slot_id, return_slot_key=True)
            if resident_info and slot_id:
                # Try to get again after rebuild
                try:
                    async with resident_info.solve_lock:
                        cookies = await self._get_browser_cookies(
                            label=f"refresh_session_get_cookies_after_rebuild:{slot_id}",
                        )
                    for cookie in cookies:
                        if cookie.name == "__Secure-next-auth.session-token":
                            resident_info.last_used_at = time.time()
                            self._remember_project_affinity(project_id, slot_id, resident_info)
                            self._resident_error_streaks.pop(slot_id, None)
                            debug_logger.log_info(f"[BrowserCaptcha] ✅ Session Token obtained successfully after rebuild")
                            return cookie.value
                except Exception:
                    pass
            
            return None

    # ========== Status Query ==========

    def is_resident_mode_active(self) -> bool:
        """Check if any resident tab is active"""
        return len(self._resident_tabs) > 0 or self._running

    def get_resident_count(self) -> int:
        """Get current number of resident tabs"""
        return len(self._resident_tabs)

    def get_resident_project_ids(self) -> list[str]:
        """Get list of all current shared resident tab slot_ids."""
        return list(self._resident_tabs.keys())

    def get_resident_project_id(self) -> Optional[str]:
        """Get the first slot_id in the current shared pool (for backward compatibility)."""
        if self._resident_tabs:
            return next(iter(self._resident_tabs.keys()))
        return self.resident_project_id

    async def get_custom_token(
        self,
        website_url: str,
        website_key: str,
        action: str = "homepage",
        enterprise: bool = False,
    ) -> Optional[str]:
        """Execute reCAPTCHA for any site, used for score testing and similar scenarios.

        Unlike the normal legacy mode, this reuses the same resident tab to avoid cold-starting a new tab each time.
        """
        await self.initialize()
        self._last_fingerprint = None

        cache_key = f"{website_url}|{website_key}|{1 if enterprise else 0}"
        warmup_seconds = float(getattr(config, "browser_score_test_warmup_seconds", 12) or 12)
        per_request_settle_seconds = float(
            getattr(config, "browser_score_test_settle_seconds", 2.5) or 2.5
        )
        max_retries = 2

        async with self._custom_lock:
            for attempt in range(max_retries):
                start_time = time.time()
                custom_info = self._custom_tabs.get(cache_key)
                tab = custom_info.get("tab") if isinstance(custom_info, dict) else None

                try:
                    if tab is None:
                        debug_logger.log_info(f"[BrowserCaptcha] [Custom] Creating resident test tab: {website_url}")
                        tab = await self._browser_get(
                            website_url,
                            label="custom_browser_get",
                            new_tab=True,
                        )
                        custom_info = {
                            "tab": tab,
                            "recaptcha_ready": False,
                            "warmed_up": False,
                            "created_at": time.time(),
                        }
                        self._custom_tabs[cache_key] = custom_info

                    page_loaded = False
                    for _ in range(20):
                        ready_state = await self._tab_evaluate(
                            tab,
                            "document.readyState",
                            label="custom_document_ready",
                            timeout_seconds=2.0,
                        )
                        if ready_state == "complete":
                            page_loaded = True
                            break
                        await tab.sleep(0.5)

                    if not page_loaded:
                        raise RuntimeError("Custom page load timeout")

                    if not custom_info.get("recaptcha_ready"):
                        recaptcha_ready = await self._wait_for_custom_recaptcha(
                            tab=tab,
                            website_key=website_key,
                            enterprise=enterprise,
                        )
                        if not recaptcha_ready:
                            raise RuntimeError("Custom reCAPTCHA cannot load")
                        custom_info["recaptcha_ready"] = True

                    try:
                        await self._tab_evaluate(tab, """
                            (() => {
                                try {
                                    const body = document.body || document.documentElement;
                                    const width = window.innerWidth || 1280;
                                    const height = window.innerHeight || 720;
                                    const x = Math.max(24, Math.floor(width * 0.38));
                                    const y = Math.max(24, Math.floor(height * 0.32));
                                    const moveEvent = new MouseEvent('mousemove', {
                                        bubbles: true,
                                        clientX: x,
                                        clientY: y
                                    });
                                    const overEvent = new MouseEvent('mouseover', {
                                        bubbles: true,
                                        clientX: x,
                                        clientY: y
                                    });
                                    window.focus();
                                    window.dispatchEvent(new Event('focus'));
                                    document.dispatchEvent(moveEvent);
                                    document.dispatchEvent(overEvent);
                                    if (body) {
                                        body.dispatchEvent(moveEvent);
                                        body.dispatchEvent(overEvent);
                                    }
                                    window.scrollTo(0, Math.min(320, document.body?.scrollHeight || 320));
                                } catch (e) {}
                            })()
                        """, label="custom_pre_warm_interaction", timeout_seconds=6.0)
                    except Exception:
                        pass

                    if not custom_info.get("warmed_up"):
                        if warmup_seconds > 0:
                            debug_logger.log_info(
                                f"[BrowserCaptcha] [Custom] First warmup test page, executing token after {warmup_seconds:.1f}s"
                            )
                            try:
                                await self._tab_evaluate(tab, """
                                    (() => {
                                        try {
                                            window.scrollTo(0, Math.min(240, document.body.scrollHeight || 240));
                                            window.dispatchEvent(new Event('mousemove'));
                                            window.dispatchEvent(new Event('focus'));
                                        } catch (e) {}
                                    })()
                                """, label="custom_warmup_interaction", timeout_seconds=6.0)
                            except Exception:
                                pass
                            await tab.sleep(warmup_seconds)
                        custom_info["warmed_up"] = True
                    elif per_request_settle_seconds > 0:
                        debug_logger.log_info(
                            f"[BrowserCaptcha] [Custom] Reusing test tab, extra wait {per_request_settle_seconds:.1f}s before execution"
                        )
                        await tab.sleep(per_request_settle_seconds)

                    debug_logger.log_info(f"[BrowserCaptcha] [Custom] Using resident test tab to execute verification (action: {action})...")
                    token = await self._execute_custom_recaptcha_on_tab(
                        tab=tab,
                        website_key=website_key,
                        action=action,
                        enterprise=enterprise,
                    )

                    duration_ms = (time.time() - start_time) * 1000
                    if token:
                        extracted_fingerprint = await self._extract_tab_fingerprint(tab)
                        if not extracted_fingerprint:
                            try:
                                fallback_ua = await self._tab_evaluate(
                                    tab,
                                    "navigator.userAgent || ''",
                                    label="custom_fallback_ua",
                                )
                                fallback_lang = await self._tab_evaluate(
                                    tab,
                                    "navigator.language || ''",
                                    label="custom_fallback_lang",
                                )
                                extracted_fingerprint = {
                                    "user_agent": fallback_ua or "",
                                    "accept_language": fallback_lang or "",
                                    "proxy_url": self._proxy_url,
                                }
                            except Exception:
                                extracted_fingerprint = None
                        self._last_fingerprint = extracted_fingerprint
                        debug_logger.log_info(
                            f"[BrowserCaptcha] [Custom] ✅ Resident test tab token obtained successfully (duration {duration_ms:.0f}ms)"
                        )
                        return token

                    raise RuntimeError("Custom token acquisition failed (returned null)")
                except Exception as e:
                    debug_logger.log_warning(
                        f"[BrowserCaptcha] [Custom] Attempt {attempt + 1}/{max_retries} failed: {str(e)}"
                    )
                    stale_info = self._custom_tabs.pop(cache_key, None)
                    stale_tab = stale_info.get("tab") if isinstance(stale_info, dict) else None
                    if stale_tab:
                        await self._close_tab_quietly(stale_tab)
                    if attempt >= max_retries - 1:
                        debug_logger.log_error(f"[BrowserCaptcha] [Custom] Token acquisition exception: {str(e)}")
                        return None

            return None

    async def get_custom_score(
        self,
        website_url: str,
        website_key: str,
        verify_url: str,
        action: str = "homepage",
        enterprise: bool = False,
    ) -> Dict[str, Any]:
        """Get token and directly verify page score in the same resident tab."""
        token_started_at = time.time()
        token = await self.get_custom_token(
            website_url=website_url,
            website_key=website_key,
            action=action,
            enterprise=enterprise,
        )
        token_elapsed_ms = int((time.time() - token_started_at) * 1000)

        if not token:
            return {
                "token": None,
                "token_elapsed_ms": token_elapsed_ms,
                "verify_mode": "browser_page",
                "verify_elapsed_ms": 0,
                "verify_http_status": None,
                "verify_result": {},
            }

        cache_key = f"{website_url}|{website_key}|{1 if enterprise else 0}"
        async with self._custom_lock:
            custom_info = self._custom_tabs.get(cache_key)
            tab = custom_info.get("tab") if isinstance(custom_info, dict) else None
            if tab is None:
                raise RuntimeError("Page score test tab does not exist")
            verify_payload = await self._verify_score_on_tab(tab, token, verify_url)

        return {
            "token": token,
            "token_elapsed_ms": token_elapsed_ms,
            **verify_payload,
        }
