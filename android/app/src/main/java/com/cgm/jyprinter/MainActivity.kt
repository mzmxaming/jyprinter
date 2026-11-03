package com.cgm.jyprinter

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.activity.viewModels
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.core.content.ContextCompat
import androidx.lifecycle.lifecycleScope
import com.cgm.jyprinter.ui.AppRoot
import com.cgm.jyprinter.ui.FileImporter
import com.cgm.jyprinter.ui.theme.JyPrinterTheme
import com.cgm.jyprinter.vm.MainViewModel
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/**
 * 应用唯一入口。
 *
 * 除常规启动外，还接收系统「用其他应用打开 / 分享」传入的文件（微信即通过
 * VIEW + content:// 的方式发送），把文件复制到缓存后交给主界面直接打印。
 *
 * 对 file:// 来源（部分文件管理器）：Android 12 及以下按需申请存储读取权限后重试。
 */
class MainActivity : ComponentActivity() {

    private val vm: MainViewModel by viewModels()

    /** 等待存储权限授予后再导入的 URI */
    private var pendingUri: Uri? = null

    private val storagePermission =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
            val uri = pendingUri
            pendingUri = null
            if (granted && uri != null) {
                importFile(uri)
            } else {
                vm.postMessage("没有存储读取权限，无法打开该文件")
            }
        }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        if (savedInstanceState == null) {
            handleIntent(intent)
        }
        setContent {
            JyPrinterTheme {
                val state by vm.state.collectAsState()
                AppRoot(state = state, vm = vm)
            }
        }
    }

    /**
     * 处理新意图（singleTask 模式下从微信再次打开文件时走这里）。
     *
     * @param intent 新意图
     * @return 无
     */
    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        handleIntent(intent)
    }

    /**
     * 解析外部传入的文件：必要时先申请权限，再复制到缓存。
     *
     * @param intent 意图
     * @return 无
     */
    private fun handleIntent(intent: Intent?) {
        val action = intent?.action ?: return
        val uri = when (action) {
            Intent.ACTION_VIEW -> intent.data
            Intent.ACTION_SEND -> parcelableUri(intent)
            Intent.ACTION_SEND_MULTIPLE -> multipleUris(intent).firstOrNull()
            else -> null
        } ?: return

        if (needStoragePermission(uri)) {
            pendingUri = uri
            storagePermission.launch(Manifest.permission.READ_EXTERNAL_STORAGE)
            return
        }
        importFile(uri)
    }

    /**
     * 判断是否需要申请存储读取权限（仅 file:// 且 Android 9 及以下未授权时）。
     *
     * Android 10 起分区存储禁止通过路径直读其他应用的文件，此时不再申请。
     *
     * @param uri 文件 URI
     * @return 是否需要申请
     */
    private fun needStoragePermission(uri: Uri): Boolean {
        if (!"file".equals(uri.scheme, ignoreCase = true)) return false
        if (Build.VERSION.SDK_INT > Build.VERSION_CODES.P) return false
        val granted = ContextCompat.checkSelfPermission(
            this,
            Manifest.permission.READ_EXTERNAL_STORAGE,
        ) == PackageManager.PERMISSION_GRANTED
        return !granted
    }

    /**
     * 把文件复制到缓存并交给主界面。
     *
     * @param uri 文件 URI
     * @return 无
     */
    private fun importFile(uri: Uri) {
        lifecycleScope.launch {
            runCatching {
                val (name, size) = withContext(Dispatchers.IO) {
                    FileImporter.queryInfo(this@MainActivity, uri)
                }
                val file = withContext(Dispatchers.IO) {
                    FileImporter.copyToCache(this@MainActivity, uri, name)
                }
                vm.setPendingFile(file, name, if (size > 0) size else file.length(), fromExternal = true)
            }.onFailure { error ->
                val detail = error.message.orEmpty()
                val friendly = if (detail.contains("EACCES") || error is SecurityException) {
                    "无法读取该文件：Android 10 起禁止按路径读取，请从微信或文件管理器中「分享」到本应用"
                } else {
                    "读取文件失败：$detail"
                }
                vm.postMessage(friendly)
            }
        }
    }

    /**
     * 读取单文件分享的 URI。
     *
     * @param intent 意图
     * @return 文件 URI 或 null
     */
    @Suppress("DEPRECATION")
    private fun parcelableUri(intent: Intent): Uri? =
        intent.getParcelableExtra(Intent.EXTRA_STREAM)

    /**
     * 读取多文件分享的 URI 列表。
     *
     * @param intent 意图
     * @return 文件 URI 列表
     */
    @Suppress("DEPRECATION")
    private fun multipleUris(intent: Intent): List<Uri> =
        intent.getParcelableArrayListExtra<Uri>(Intent.EXTRA_STREAM)?.filterNotNull() ?: emptyList()
}
