package com.cgm.jyprinter.ui

import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.rounded.Close
import androidx.compose.material.icons.rounded.FolderOpen
import androidx.compose.material.icons.rounded.Print
import androidx.compose.material.icons.rounded.Refresh
import androidx.compose.material.icons.rounded.Settings
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.RadioButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Slider
import androidx.compose.material3.Surface
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.cgm.jyprinter.data.PrintStats
import com.cgm.jyprinter.data.PrintTask
import com.cgm.jyprinter.data.StatBucket
import com.cgm.jyprinter.ui.theme.BrandBlue
import com.cgm.jyprinter.vm.MainViewModel
import com.cgm.jyprinter.vm.UiState
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlin.math.roundToInt

/**
 * 主界面：待打印文件、全部打印参数、提交按钮与最近任务。
 *
 * @param state 界面状态
 * @param vm 状态持有者
 * @param snackbar 提示宿主
 * @return 无
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun HomeScreen(
    state: UiState,
    vm: MainViewModel,
    snackbar: SnackbarHostState,
) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    var showSettings by remember { mutableStateOf(false) }
    var showSplit by remember { mutableStateOf(false) }

    val picker = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        if (uri == null) return@rememberLauncherForActivityResult
        scope.launch {
            runCatching {
                val (name, size) = FileImporter.queryInfo(context, uri)
                val file = withContext(Dispatchers.IO) {
                    FileImporter.copyToCache(context, uri, name)
                }
                vm.setPendingFile(file, name, if (size > 0) size else file.length())
            }.onFailure { error ->
                vm.postMessage("读取文件失败：${error.message}")
            }
        }
    }

    Scaffold(
        containerColor = MaterialTheme.colorScheme.background,
        snackbarHost = { SnackbarHost(snackbar) },
        topBar = {
            TopAppBar(
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = MaterialTheme.colorScheme.background,
                ),
                title = {
                    Column {
                        Text(
                            text = "网络打印",
                            style = MaterialTheme.typography.titleLarge,
                        )
                        Text(
                            text = state.serverLabel.ifBlank { "未设置服务地址" },
                            style = MaterialTheme.typography.labelMedium,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                            maxLines = 1,
                            overflow = TextOverflow.Ellipsis,
                        )
                    }
                },
                actions = {
                    IconButton(onClick = { vm.refreshTasks(); vm.loadPrinters() }) {
                        Icon(Icons.Rounded.Refresh, contentDescription = "刷新")
                    }
                    IconButton(onClick = { showSettings = true }) {
                        Icon(Icons.Rounded.Settings, contentDescription = "设置")
                    }
                },
            )
        },
    ) { padding ->
        LazyColumn(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding),
            contentPadding = PaddingValues(start = 16.dp, end = 16.dp, top = 4.dp, bottom = 28.dp),
            verticalArrangement = Arrangement.spacedBy(14.dp),
        ) {
            item {
                GradientHeader(
                    title = "准备就绪",
                    subtitle = "${state.account}　·　${state.selectedPrinter.ifBlank { "默认打印机" }}",
                    trailing = {
                        val active = state.tasks.count {
                            it.status == "queued" || it.status == "printing" || it.status == "review"
                        }
                        Surface(
                            shape = RoundedCornerShape(50),
                            color = Color.White.copy(alpha = 0.18f),
                        ) {
                            Text(
                                text = "队列 $active",
                                style = MaterialTheme.typography.labelMedium,
                                color = Color.White,
                                modifier = Modifier.padding(horizontal = 12.dp, vertical = 6.dp),
                            )
                        }
                    },
                )
            }

            item {
                PendingFileCard(
                    state = state,
                    onPick = { picker.launch(arrayOf("*/*")) },
                    onClear = vm::clearPendingFile,
                    onSplit = { showSplit = true },
                )
            }

            item { PrintOptionsCard(state = state, vm = vm) }

            item {
                Button(
                    onClick = vm::print,
                    enabled = state.pendingFile != null && !state.printing,
                    shape = RoundedCornerShape(18.dp),
                    colors = ButtonDefaults.buttonColors(
                        containerColor = BrandBlue,
                        contentColor = Color.White,
                    ),
                    modifier = Modifier
                        .fillMaxWidth()
                        .height(56.dp),
                ) {
                    if (state.printing) {
                        CircularProgressIndicator(
                            modifier = Modifier.size(20.dp),
                            strokeWidth = 2.dp,
                            color = Color.White,
                        )
                        Text(text = "正在提交…", modifier = Modifier.padding(start = 10.dp))
                    } else {
                        Icon(Icons.Rounded.Print, contentDescription = null)
                        Text(
                            text = "立即打印",
                            style = MaterialTheme.typography.titleMedium,
                            modifier = Modifier.padding(start = 8.dp),
                        )
                    }
                }
            }

            item { TasksCard(state = state, vm = vm) }

            state.stats?.let { stats ->
                item { StatsCard(stats = stats) }
            }
        }
    }

    if (showSplit) {
        A3SplitDialog(
            onDismiss = { showSplit = false },
            onConfirm = { ratios ->
                showSplit = false
                vm.splitA3(ratios)
            },
        )
    }

    if (showSettings) {
        SettingsDialog(
            state = state,
            onDismiss = { showSettings = false },
            onSave = { server, user, password ->
                showSettings = false
                vm.login(server, user, password, remember = true, autoLogin = true)
            },
            onLogout = {
                showSettings = false
                vm.logout()
            },
        )
    }
}

/**
 * 待打印文件卡片。
 *
 * @param state 界面状态
 * @param onPick 选择文件回调
 * @param onClear 清除文件回调
 * @return 无
 */
@Composable
private fun PendingFileCard(
    state: UiState,
    onPick: () -> Unit,
    onClear: () -> Unit,
    onSplit: () -> Unit,
) {
    SectionCard(
        title = "待打印文件",
        subtitle = when {
            state.pendingFile?.fromExternal == true -> "来自微信等应用的文件"
            else -> "支持 PDF、Office、WPS、图片与文本，共 20 种格式"
        },
        trailing = {
            TextButton(onClick = onPick) {
                Icon(Icons.Rounded.FolderOpen, contentDescription = null, modifier = Modifier.size(18.dp))
                Text(text = "选择文件", modifier = Modifier.padding(start = 4.dp))
            }
        },
    ) {
        val file = state.pendingFile
        if (file == null) {
            Box(
                modifier = Modifier
                    .fillMaxWidth()
                    .background(
                        MaterialTheme.colorScheme.surfaceVariant,
                        RoundedCornerShape(16.dp),
                    )
                    .padding(horizontal = 14.dp, vertical = 20.dp),
            ) {
                Column {
                    Text(
                        text = "尚未选择文件",
                        style = MaterialTheme.typography.bodyLarge,
                        fontWeight = FontWeight.Medium,
                    )
                    Text(
                        text = "在微信中打开文件 →「用其他应用打开」→ 选择「网络打印」即可自动带入",
                        style = MaterialTheme.typography.labelMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.padding(top = 6.dp),
                    )
                }
            }
            return@SectionCard
        }
        Row(verticalAlignment = Alignment.CenterVertically) {
            FileBadge(ext = file.ext)
            Column(
                modifier = Modifier
                    .weight(1f)
                    .padding(start = 12.dp),
            ) {
                Text(
                    text = file.displayName,
                    style = MaterialTheme.typography.bodyLarge,
                    fontWeight = FontWeight.Medium,
                    maxLines = 2,
                    overflow = TextOverflow.Ellipsis,
                )
                Text(
                    text = "${file.ext.uppercase()}　·　${file.sizeText}",
                    style = MaterialTheme.typography.labelMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(top = 3.dp),
                )
            }
            IconButton(onClick = onClear) {
                Icon(Icons.Rounded.Close, contentDescription = "移除文件")
            }
        }
        if (file.ext == "pdf") {
            TextButton(onClick = onSplit, enabled = !state.splitting) {
                if (state.splitting) {
                    CircularProgressIndicator(
                        modifier = Modifier.size(16.dp),
                        strokeWidth = 2.dp,
                    )
                    Text(text = "正在拆分…", modifier = Modifier.padding(start = 6.dp))
                } else {
                    Text(text = "A3 拆成 A4（矢量）")
                }
            }
        }
    }
}

/**
 * 打印参数卡片：与网页端 `/api/upload` 的参数一一对应。
 *
 * @param state 界面状态
 * @param vm 状态持有者
 * @return 无
 */
@Composable
private fun PrintOptionsCard(state: UiState, vm: MainViewModel) {
    SectionCard(title = "打印设置", subtitle = "参数与服务端网页端完全一致") {
        OptionLabel("打印机")
        LabeledDropdown(
            label = "目标打印机",
            value = state.selectedPrinter,
            options = state.printers.map {
                it.name to if (it.isDefault) "${it.name}（默认）" else it.name
            }.ifEmpty { listOf("" to "默认打印机") },
            onSelect = vm::selectPrinter,
        )

        Spacer(modifier = Modifier.height(14.dp))

        Row(
            modifier = Modifier.fillMaxWidth(),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.SpaceBetween,
        ) {
            Column(modifier = Modifier.weight(1f)) {
                Text(text = "份数", style = MaterialTheme.typography.bodyMedium, fontWeight = FontWeight.Medium)
                Text(
                    text = "每份文件打印的张数",
                    style = MaterialTheme.typography.labelMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            CopiesStepper(value = state.copies, onDelta = vm::changeCopies)
        }

        Spacer(modifier = Modifier.height(14.dp))

        OptionLabel(if (state.duplexSupported) "单双面" else "单双面（当前打印机不支持双面）")
        SegmentedRow(
            options = listOf("simplex" to "单面", "vertical" to "双面"),
            selected = state.duplex,
            enabled = state.duplexSupported,
            onSelect = vm::setDuplex,
        )

        Spacer(modifier = Modifier.height(14.dp))

        OptionLabel("纸张")
        LabeledDropdown(
            label = "纸张尺寸",
            value = state.paper,
            options = paperOptions(state.papers),
            onSelect = vm::setPaper,
        )

        Spacer(modifier = Modifier.height(14.dp))

        OptionLabel("页码范围")
        OutlinedTextField(
            value = state.pageRange,
            onValueChange = vm::setPageRange,
            singleLine = true,
            placeholder = { Text("留空表示全部，示例：1-3,5") },
            shape = RoundedCornerShape(14.dp),
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Text),
            modifier = Modifier.fillMaxWidth(),
        )

        Spacer(modifier = Modifier.height(14.dp))

        OptionLabel("缩放")
        SegmentedRow(
            options = listOf("fit" to "适应页面", "actual" to "实际大小", "custom" to "自定义"),
            selected = state.scale,
            onSelect = vm::setScale,
        )
        if (state.scale == "custom") {
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(top = 8.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(
                    text = "${state.scalePercent}%",
                    style = MaterialTheme.typography.bodyMedium,
                    fontWeight = FontWeight.Medium,
                    modifier = Modifier.padding(end = 8.dp),
                )
                Slider(
                    value = state.scalePercent.toFloat(),
                    onValueChange = { vm.setScalePercent(it.roundToInt()) },
                    valueRange = 10f..400f,
                    modifier = Modifier.weight(1f),
                )
            }
        }

        Spacer(modifier = Modifier.height(14.dp))

        OptionLabel("颜色模式")
        SegmentedRow(
            options = listOf("color" to "彩色", "mono" to "黑白"),
            selected = state.color,
            onSelect = vm::setColor,
        )

        Spacer(modifier = Modifier.height(14.dp))

        OptionLabel("打印质量")
        SegmentedRow(
            options = listOf("draft" to "草稿", "normal" to "标准", "high" to "高清"),
            selected = state.quality,
            onSelect = vm::setQuality,
        )

        Spacer(modifier = Modifier.height(14.dp))

        OptionLabel("出纸方向")
        SegmentedRow(
            options = listOf("auto" to "自动", "portrait" to "纵向", "landscape" to "横向"),
            selected = state.orientation,
            onSelect = vm::setOrientation,
        )

        Spacer(modifier = Modifier.height(14.dp))

        Row(
            modifier = Modifier.fillMaxWidth(),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.SpaceBetween,
        ) {
            Column(modifier = Modifier.weight(1f)) {
                Text(
                    text = "表格适应一页宽",
                    style = MaterialTheme.typography.bodyMedium,
                    fontWeight = FontWeight.Medium,
                )
                Text(
                    text = "Office / WPS 表格转换时按页宽缩放（仅 Windows）",
                    style = MaterialTheme.typography.labelMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            Switch(checked = state.fitTable, onCheckedChange = vm::setFitTable)
        }
    }
}

/**
 * 最近任务卡片。
 *
 * @param state 界面状态
 * @param vm 状态持有者
 * @return 无
 */
@Composable
private fun TasksCard(state: UiState, vm: MainViewModel) {
    SectionCard(
        title = "最近任务",
        subtitle = "状态每 3 秒自动刷新",
        trailing = {
            TextButton(onClick = vm::refreshTasks, enabled = !state.loadingTasks) {
                Text(if (state.loadingTasks) "刷新中…" else "刷新")
            }
        },
    ) {
        if (state.tasks.isEmpty()) {
            Text(
                text = "暂无打印任务",
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            return@SectionCard
        }
        state.tasks.take(8).forEachIndexed { index, task ->
            if (index > 0) {
                HorizontalDivider(
                    modifier = Modifier.padding(vertical = 10.dp),
                    color = MaterialTheme.colorScheme.outlineVariant,
                )
            }
            TaskRow(task = task, vm = vm)
        }
    }
}

/**
 * 单条任务。
 *
 * @param task 任务
 * @param vm 状态持有者
 * @return 无
 */
@Composable
private fun TaskRow(task: PrintTask, vm: MainViewModel) {
    Column(modifier = Modifier.fillMaxWidth()) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(
                text = task.filename,
                style = MaterialTheme.typography.bodyMedium,
                fontWeight = FontWeight.Medium,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
                modifier = Modifier.weight(1f),
            )
            Box(modifier = Modifier.padding(start = 8.dp)) {
                StatusPill(status = task.status, text = task.statusText)
            }
        }
        Text(
            text = buildString {
                if (task.sizeText.isNotBlank()) append(task.sizeText)
                if (task.paramsText.isNotBlank()) {
                    if (isNotEmpty()) append("　·　")
                    append(task.paramsText)
                }
            },
            style = MaterialTheme.typography.labelMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.padding(top = 3.dp),
            maxLines = 2,
            overflow = TextOverflow.Ellipsis,
        )
        if (task.message.isNotBlank() && task.status == "failed") {
            Text(
                text = task.message,
                style = MaterialTheme.typography.labelMedium,
                color = MaterialTheme.colorScheme.error,
                modifier = Modifier.padding(top = 3.dp),
                maxLines = 2,
                overflow = TextOverflow.Ellipsis,
            )
        }
        when (task.status) {
            "queued", "review" -> {
                Text(
                    text = "取消任务",
                    style = MaterialTheme.typography.labelMedium,
                    color = MaterialTheme.colorScheme.primary,
                    modifier = Modifier
                        .padding(top = 6.dp)
                        .clickable { vm.cancelTask(task.id) },
                )
            }
            "failed", "canceled" -> {
                Text(
                    text = "重试",
                    style = MaterialTheme.typography.labelMedium,
                    color = MaterialTheme.colorScheme.primary,
                    modifier = Modifier
                        .padding(top = 6.dp)
                        .clickable { vm.retryTask(task.id) },
                )
            }
        }
    }
}

/**
 * 打印统计卡片（仅管理员可见，服务端返回 403 时整卡隐藏）。
 *
 * @param stats 统计数据
 * @return 无
 */
@Composable
private fun StatsCard(stats: PrintStats) {
    SectionCard(title = "打印统计", subtitle = "只统计已完成任务，纸张数按单双面估算") {
        Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            StatCell(bucket = stats.today, modifier = Modifier.weight(1f))
            StatCell(bucket = stats.week, modifier = Modifier.weight(1f))
            StatCell(bucket = stats.month, modifier = Modifier.weight(1f))
        }
    }
}

/**
 * 单个统计窗口。
 *
 * @param bucket 统计桶
 * @param modifier 布局修饰符
 * @return 无
 */
@Composable
private fun StatCell(bucket: StatBucket, modifier: Modifier = Modifier) {
    Surface(
        modifier = modifier,
        shape = RoundedCornerShape(16.dp),
        color = MaterialTheme.colorScheme.surfaceVariant,
    ) {
        Column(modifier = Modifier.padding(horizontal = 12.dp, vertical = 10.dp)) {
            Text(
                text = bucket.label,
                style = MaterialTheme.typography.labelMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Text(
                text = "${bucket.pages}",
                style = MaterialTheme.typography.titleLarge,
                fontWeight = FontWeight.SemiBold,
                modifier = Modifier.padding(top = 2.dp),
            )
            Text(
                text = "${bucket.sheets} 张纸 · ${bucket.copies} 份",
                style = MaterialTheme.typography.labelMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}

/**
 * A3→A4 拆分对话框：选择裁切方式后提交服务端矢量拆分。
 *
 * @param onDismiss 取消回调
 * @param onConfirm 确认回调，参数为每页裁切比例
 * @return 无
 */
@Composable
private fun A3SplitDialog(
    onDismiss: () -> Unit,
    onConfirm: (List<Double>) -> Unit,
) {
    var mode by remember { mutableStateOf("half") }
    var custom by remember { mutableStateOf("") }

    val ratios: List<Double> = when (mode) {
        "half" -> listOf(0.5)
        "third" -> listOf(1.0 / 3, 2.0 / 3)
        else -> custom.split(',', '，')
            .mapNotNull { it.trim().toDoubleOrNull() }
            .filter { it > 0.0 && it < 1.0 }
    }

    AlertDialog(
        onDismissRequest = onDismiss,
        shape = RoundedCornerShape(24.dp),
        title = { Text(text = "A3 拆成 A4") },
        text = {
            Column {
                Text(
                    text = "由服务端做矢量裁剪，文字与图形保持清晰；拆分结果会替换当前待打印文件。",
                    style = MaterialTheme.typography.labelMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                Spacer(modifier = Modifier.height(8.dp))
                listOf(
                    "half" to "对半拆（左右各一页 A4）",
                    "third" to "三等分",
                    "custom" to "自定义裁切位置",
                ).forEach { option ->
                    Row(
                        modifier = Modifier
                            .fillMaxWidth()
                            .clickable { mode = option.first },
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        RadioButton(
                            selected = mode == option.first,
                            onClick = { mode = option.first },
                        )
                        Text(text = option.second, style = MaterialTheme.typography.bodyMedium)
                    }
                }
                if (mode == "custom") {
                    OutlinedTextField(
                        value = custom,
                        onValueChange = { custom = it },
                        label = { Text("比例，如 0.5 或 0.33,0.66") },
                        singleLine = true,
                        shape = RoundedCornerShape(14.dp),
                        modifier = Modifier.fillMaxWidth(),
                    )
                }
            }
        },
        confirmButton = {
            TextButton(
                onClick = { onConfirm(ratios) },
                enabled = ratios.isNotEmpty(),
            ) {
                Text(text = "开始拆分")
            }
        },
        dismissButton = {
            TextButton(onClick = onDismiss) { Text(text = "取消") }
        },
    )
}
