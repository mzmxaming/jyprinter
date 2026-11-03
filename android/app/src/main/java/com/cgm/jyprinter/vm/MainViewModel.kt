package com.cgm.jyprinter.vm

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.cgm.jyprinter.data.ApiClient
import com.cgm.jyprinter.data.MeInfo
import com.cgm.jyprinter.data.Paper
import com.cgm.jyprinter.data.PendingFile
import com.cgm.jyprinter.data.Prefs
import com.cgm.jyprinter.data.PrintOptions
import com.cgm.jyprinter.data.PrintStats
import com.cgm.jyprinter.data.Printer
import com.cgm.jyprinter.data.PrintTask
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import java.io.File

/**
 * 界面状态。
 *
 * @param booting 启动中（正在尝试自动登录）
 * @param loggedIn 是否已登录
 * @param account 当前账号
 * @param roleText 角色中文名
 * @param serverUrl 服务地址
 * @param loginError 登录错误提示
 * @param loggingIn 登录请求进行中
 * @param printers 打印机列表
 * @param selectedPrinter 当前选中的打印机
 * @param papers 当前打印机支持的纸张
 * @param paper 当前选中的纸张
 * @param duplexSupported 当前打印机是否支持双面
 * @param copies 份数
 * @param duplex simplex / vertical
 * @param pageRange 页码范围
 * @param scale fit / actual / custom
 * @param scalePercent 自定义缩放百分比
 * @param color color / mono
 * @param quality draft / normal / high
 * @param orientation auto / portrait / landscape
 * @param fitTable 表格类文档是否设为「适应一页宽」
 * @param pendingFile 待打印文件
 * @param tasks 最近任务
 * @param stats 打印量统计（非管理员为 null）
 * @param printing 正在上传并提交打印
 * @param splitting 正在执行 A3→A4 拆分
 * @param loadingTasks 正在刷新任务
 * @param message 一次性提示（Snackbar）
 */
data class UiState(
    val booting: Boolean = true,
    val loggedIn: Boolean = false,
    val account: String = "",
    val roleText: String = "",
    val serverUrl: String = "",
    val loginError: String? = null,
    val loggingIn: Boolean = false,
    val printers: List<Printer> = emptyList(),
    val selectedPrinter: String = "",
    val papers: List<Paper> = listOf(Paper("", "A4")),
    val paper: String = "A4",
    val duplexSupported: Boolean = true,
    val copies: Int = 1,
    val duplex: String = "simplex",
    val pageRange: String = "",
    val scale: String = "fit",
    val scalePercent: Int = 100,
    val color: String = "color",
    val quality: String = "normal",
    val orientation: String = "auto",
    val fitTable: Boolean = true,
    val pendingFile: PendingFile? = null,
    val tasks: List<PrintTask> = emptyList(),
    val stats: PrintStats? = null,
    val printing: Boolean = false,
    val splitting: Boolean = false,
    val loadingTasks: Boolean = false,
    val message: String? = null,
) {
    /** 服务地址去掉协议后的展示文本 */
    val serverLabel: String
        get() = serverUrl.removePrefix("http://").removePrefix("https://")
}

/**
 * 主界面与登录逻辑的状态持有者。
 *
 * @param app 应用实例
 */
class MainViewModel(app: Application) : AndroidViewModel(app) {

    private val prefs = Prefs(app)
    private val api = ApiClient(prefs)

    private val _state = MutableStateFlow(
        UiState(
            serverUrl = prefs.serverUrl,
            account = prefs.username,
            selectedPrinter = prefs.lastPrinter,
        )
    )
    val state: StateFlow<UiState> = _state.asStateFlow()

    private var pollJob: Job? = null

    init {
        api.setBaseUrl(prefs.serverUrl)
        tryAutoLogin()
        startPolling()
    }

    /**
     * 尝试自动登录：优先用已保存的账号密码，其次复用已有会话 Cookie。
     *
     * @return 无
     */
    private fun tryAutoLogin() {
        val server = prefs.serverUrl
        if (server.isBlank()) {
            _state.value = _state.value.copy(booting = false)
            return
        }
        val user = prefs.username
        val password = prefs.password
        if (prefs.autoLogin && user.isNotBlank() && password.isNotBlank()) {
            login(server, user, password, remember = true, autoLogin = true, silent = true)
            return
        }
        viewModelScope.launch {
            val me = api.me()
            if (me != null) {
                onLoggedIn(me)
            } else {
                _state.value = _state.value.copy(booting = false)
            }
        }
    }

    /**
     * 执行登录。
     *
     * @param server 服务地址
     * @param username 账号
     * @param password 密码
     * @param remember 是否记住密码
     * @param autoLogin 是否下次自动登录
     * @param silent 静默登录（自动登录失败时也要把界面切到登录页）
     * @return 无
     */
    fun login(
        server: String,
        username: String,
        password: String,
        remember: Boolean,
        autoLogin: Boolean,
        silent: Boolean = false,
    ) {
        val normalized = api.normalize(server)
        if (normalized.isBlank()) {
            _state.value = _state.value.copy(booting = false, loginError = "请填写服务地址")
            return
        }
        if (username.isBlank() || password.isBlank()) {
            _state.value = _state.value.copy(booting = false, loginError = "请填写账号与密码")
            return
        }
        viewModelScope.launch {
            _state.value = _state.value.copy(loggingIn = true, loginError = null)
            val result = runCatching {
                api.setBaseUrl(normalized)
                api.login(username.trim(), password)
            }
            result.onSuccess { me ->
                prefs.serverUrl = normalized
                prefs.username = username.trim()
                prefs.autoLogin = autoLogin
                prefs.password = if (remember) password else ""
                onLoggedIn(me)
            }.onFailure { error ->
                _state.value = _state.value.copy(
                    booting = false,
                    loggingIn = false,
                    loginError = error.message ?: "登录失败",
                )
            }
        }
    }

    /**
     * 登录成功后的统一处理：切换界面并拉取打印机与任务。
     *
     * @param me 账号信息
     * @return 无
     */
    private fun onLoggedIn(me: MeInfo) {
        _state.value = _state.value.copy(
            booting = false,
            loggedIn = true,
            loggingIn = false,
            loginError = null,
            account = me.username,
            roleText = me.roleText,
            serverUrl = api.baseUrl,
        )
        loadPrinters()
        loadStats()
        refreshTasks()
    }

    /**
     * 退出登录。
     *
     * @return 无
     */
    fun logout() {
        prefs.clearSession()
        _state.value = _state.value.copy(
            loggedIn = false,
            booting = false,
            account = prefs.username,
            roleText = "",
            pendingFile = null,
            tasks = emptyList(),
            loginError = null,
        )
    }

    /**
     * 拉取打印机列表，并恢复上次使用的打印机。
     *
     * @return 无
     */
    fun loadPrinters() {
        viewModelScope.launch {
            runCatching { api.printers() }
                .onSuccess { resp ->
                    val candidates = listOf(
                        _state.value.selectedPrinter,
                        prefs.lastPrinter,
                        resp.default,
                        resp.printers.firstOrNull()?.name.orEmpty(),
                    )
                    val target = candidates.firstOrNull { name ->
                        name.isNotBlank() && resp.printers.any { it.name == name }
                    }.orEmpty()
                    _state.value = _state.value.copy(printers = resp.printers, selectedPrinter = target)
                    prefs.lastPrinter = target
                    loadCapabilities(target)
                }
                .onFailure { postMessage(it.message ?: "获取打印机失败") }
        }
    }

    /**
     * 切换打印机。
     *
     * @param name 打印机名称
     * @return 无
     */
    fun selectPrinter(name: String) {
        if (name == _state.value.selectedPrinter) return
        _state.value = _state.value.copy(selectedPrinter = name)
        prefs.lastPrinter = name
        loadCapabilities(name)
    }

    /**
     * 查询并应用打印机能力（纸张列表与双面支持）。
     *
     * @param name 打印机名称
     * @return 无
     */
    private fun loadCapabilities(name: String) {
        viewModelScope.launch {
            runCatching { api.capabilities(name) }
                .onSuccess { caps ->
                    val names = caps.papers.map { it.name }
                    val current = _state.value.paper
                    val paper = when {
                        names.contains(current) -> current
                        names.contains("A4") -> "A4"
                        names.isNotEmpty() -> names.first()
                        else -> "A4"
                    }
                    _state.value = _state.value.copy(
                        papers = caps.papers,
                        paper = paper,
                        duplexSupported = caps.duplex,
                        duplex = if (caps.duplex) _state.value.duplex else "simplex",
                    )
                }
                .onFailure { postMessage(it.message ?: "获取打印机能力失败") }
        }
    }

    /**
     * 刷新最近任务。
     *
     * @return 无
     */
    fun refreshTasks() {
        if (!_state.value.loggedIn) return
        viewModelScope.launch {
            _state.value = _state.value.copy(loadingTasks = true)
            runCatching { api.tasks(30) }
                .onSuccess { list -> _state.value = _state.value.copy(tasks = list, loadingTasks = false) }
                .onFailure { _state.value = _state.value.copy(loadingTasks = false) }
        }
    }

    /**
     * 定时轮询任务状态，实时反映「排队中 → 打印中 → 已完成 / 失败」。
     *
     * @return 无
     */
    private fun startPolling() {
        pollJob?.cancel()
        pollJob = viewModelScope.launch {
            while (isActive) {
                delay(3000)
                if (_state.value.loggedIn) refreshTasksSilently()
            }
        }
    }

    /**
     * 静默刷新任务（不显示加载态与错误提示）。
     *
     * @return 无
     */
    private suspend fun refreshTasksSilently() {
        runCatching { api.tasks(30) }
            .onSuccess { list -> _state.value = _state.value.copy(tasks = list) }
    }

    /**
     * 设置待打印文件。
     *
     * @param file 已复制到缓存的文件
     * @param displayName 原始文件名
     * @param size 字节数
     * @param fromExternal 是否由其他应用（如微信）传入
     * @return 无
     */
    fun setPendingFile(file: File, displayName: String, size: Long, fromExternal: Boolean = false) {
        _state.value = _state.value.copy(
            pendingFile = PendingFile(file, displayName, size, fromExternal),
        )
    }

    /**
     * 清除待打印文件。
     *
     * @return 无
     */
    fun clearPendingFile() {
        _state.value = _state.value.copy(pendingFile = null)
    }

    /**
     * 修改份数。
     *
     * @param delta 增量
     * @return 无
     */
    fun changeCopies(delta: Int) {
        val value = (_state.value.copies + delta).coerceIn(1, 99)
        _state.value = _state.value.copy(copies = value)
    }

    /**
     * 设置单双面。
     *
     * @param value simplex / vertical
     * @return 无
     */
    fun setDuplex(value: String) {
        _state.value = _state.value.copy(duplex = value)
    }

    /**
     * 设置纸张。
     *
     * @param value 纸张名称
     * @return 无
     */
    fun setPaper(value: String) {
        _state.value = _state.value.copy(paper = value)
    }

    /**
     * 设置页码范围。
     *
     * @param value 用户输入
     * @return 无
     */
    fun setPageRange(value: String) {
        _state.value = _state.value.copy(pageRange = value)
    }

    /**
     * 设置缩放方式。
     *
     * @param value fit / actual / custom
     * @return 无
     */
    fun setScale(value: String) {
        _state.value = _state.value.copy(scale = value)
    }

    /**
     * 设置自定义缩放百分比。
     *
     * @param value 10~400
     * @return 无
     */
    fun setScalePercent(value: Int) {
        _state.value = _state.value.copy(scalePercent = value.coerceIn(10, 400))
    }

    /**
     * 设置颜色模式。
     *
     * @param value color / mono
     * @return 无
     */
    fun setColor(value: String) {
        _state.value = _state.value.copy(color = value)
    }

    /**
     * 设置打印质量。
     *
     * @param value draft / normal / high
     * @return 无
     */
    fun setQuality(value: String) {
        _state.value = _state.value.copy(quality = value)
    }

    /**
     * 设置出纸方向。
     *
     * @param value auto / portrait / landscape
     * @return 无
     */
    fun setOrientation(value: String) {
        _state.value = _state.value.copy(orientation = value)
    }

    /**
     * 设置表格文档是否适应一页宽。
     *
     * @param value 是否启用
     * @return 无
     */
    fun setFitTable(value: Boolean) {
        _state.value = _state.value.copy(fitTable = value)
    }

    /**
     * 查询打印量统计（仅管理员可见，失败时静默隐藏）。
     *
     * @return 无
     */
    fun loadStats() {
        if (!_state.value.loggedIn) return
        viewModelScope.launch {
            runCatching { api.stats() }
                .onSuccess { data -> _state.value = _state.value.copy(stats = data) }
        }
    }

    /**
     * 把当前 PDF 按给定比例拆分为 A4（服务端矢量裁剪），结果替换为新的待打印文件。
     *
     * @param ratios 每页的裁切比例，如 [0.5] 表示对半拆、[0.33, 0.66] 表示三等分
     * @return 无
     */
    fun splitA3(ratios: List<Double>) {
        val current = _state.value
        val pending = current.pendingFile ?: return
        if (pending.ext != "pdf") {
            postMessage("A3 拆分仅支持 PDF 文件")
            return
        }
        if (ratios.isEmpty() || ratios.any { it <= 0.0 || it >= 1.0 }) {
            postMessage("裁切比例需在 0 与 1 之间")
            return
        }
        // 服务端按「每页一个比例数组」解析，缺省页会当作整页；
        // 这里按同一比例重复足够多次，覆盖任意页数（多余项服务端会忽略）
        val one = ratios.joinToString(",") { it.toString() }
        val cuts = (1..500).joinToString(",", "[", "]") { "[$one]" }

        viewModelScope.launch {
            _state.value = _state.value.copy(splitting = true)
            runCatching { api.splitPdf(pending.file, pending.displayName, cuts) }
                .onSuccess { bytes ->
                    val context = getApplication<Application>()
                    val dir = File(context.cacheDir, "incoming").apply { mkdirs() }
                    val base = pending.displayName.substringBeforeLast('.', pending.displayName)
                    val output = File(dir, "${System.currentTimeMillis()}_${base}_A4.pdf")
                    output.writeBytes(bytes)
                    _state.value = _state.value.copy(
                        splitting = false,
                        pendingFile = PendingFile(output, "${base}_A4.pdf", output.length(), false),
                        message = "已拆分为 A4（矢量），可直接打印",
                    )
                }
                .onFailure { error ->
                    _state.value = _state.value.copy(
                        splitting = false,
                        message = error.message ?: "拆分失败",
                    )
                }
        }
    }

    /**
     * 提交打印。
     *
     * @return 无
     */
    fun print() {
        val current = _state.value
        val pending = current.pendingFile ?: return
        val range = current.pageRange.trim()
        if (range.isNotEmpty() && !isPageRangeValid(range)) {
            postMessage("页码范围格式不正确，示例：1-3,5")
            return
        }
        val options = PrintOptions(
            printer = current.selectedPrinter,
            copies = current.copies,
            duplex = if (current.duplexSupported) current.duplex else "simplex",
            paper = current.paper,
            pageRange = range,
            scale = current.scale,
            scalePercent = if (current.scale == "custom") current.scalePercent else 100,
            color = current.color,
            quality = current.quality,
            orientation = current.orientation,
            fitTable = current.fitTable,
        )
        viewModelScope.launch {
            _state.value = _state.value.copy(printing = true)
            runCatching { api.upload(pending.file, pending.displayName, options) }
                .onSuccess { result ->
                    val text = buildString {
                        if (result.tasks.isNotEmpty()) append("已提交 ${result.tasks.size} 个打印任务")
                        if (result.rejected.isNotEmpty()) {
                            if (isNotEmpty()) append("；")
                            append("${result.rejected.size} 个文件未提交：${result.rejected.first().second}")
                        }
                    }
                    _state.value = _state.value.copy(
                        printing = false,
                        pendingFile = if (result.tasks.isNotEmpty()) null else pending,
                        message = text,
                    )
                    refreshTasks()
                }
                .onFailure { error ->
                    _state.value = _state.value.copy(
                        printing = false,
                        message = error.message ?: "打印失败",
                    )
                }
        }
    }

    /**
     * 取消任务。
     *
     * @param id 任务 ID
     * @return 无
     */
    fun cancelTask(id: String) {
        viewModelScope.launch {
            runCatching { api.cancel(id) }
                .onSuccess { message ->
                    postMessage(message)
                    refreshTasks()
                }
                .onFailure { postMessage(it.message ?: "取消失败") }
        }
    }

    /**
     * 重试任务。
     *
     * @param id 任务 ID
     * @return 无
     */
    fun retryTask(id: String) {
        viewModelScope.launch {
            runCatching { api.retry(id) }
                .onSuccess { message ->
                    postMessage(message)
                    refreshTasks()
                }
                .onFailure { postMessage(it.message ?: "重试失败") }
        }
    }

    /**
     * 校验页码范围格式（与服务端一致：允许 1-3,5 / 2- / -3 与前缀符号）。
     *
     * @param text 用户输入
     * @return 是否合法
     */
    private fun isPageRangeValid(text: String): Boolean {
        val normalized = text.replace('，', ',').replace('、', ',').replace(" ", "")
        if (!normalized.matches(Regex("^[0-9,\\-]+$"))) return false
        val parts = normalized.split(',').filter { it.isNotEmpty() }
        if (parts.isEmpty()) return false
        for (part in parts) {
            val pieces = part.split('-')
            if (pieces.size > 2) return false
            if (pieces.any { it.isNotEmpty() && it.toIntOrNull() == null }) return false
            if (pieces.size == 2 && pieces.all { it.isNotEmpty() }) {
                val start = pieces[0].toIntOrNull() ?: return false
                val end = pieces[1].toIntOrNull() ?: return false
                if (start <= 0 || end <= 0 || start > end) return false
            }
        }
        return true
    }

    /**
     * 显示一次性提示。
     *
     * @param text 提示文案
     * @return 无
     */
    fun postMessage(text: String) {
        _state.value = _state.value.copy(message = text)
    }

    /**
     * 消费提示，避免重复弹出。
     *
     * @return 无
     */
    fun consumeMessage() {
        _state.value = _state.value.copy(message = null)
    }
}
