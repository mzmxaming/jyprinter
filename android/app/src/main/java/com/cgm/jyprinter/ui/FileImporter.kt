package com.cgm.jyprinter.ui

import android.content.Context
import android.net.Uri
import android.provider.OpenableColumns
import java.io.File

/**
 * 文件导入工具：把其他应用通过 content:// 传入的文件复制到应用缓存目录，
 * 避免源应用授权失效后无法读取。
 */
object FileImporter {

    /**
     * 读取文件展示名与大小。
     *
     * @param context 上下文
     * @param uri 文件 URI
     * @return 文件名（含扩展名）与字节数
     */
    fun queryInfo(context: Context, uri: Uri): Pair<String, Long> {
        var name = uri.lastPathSegment?.substringAfterLast('/') ?: "文件"
        var size = 0L
        runCatching {
            context.contentResolver.query(uri, null, null, null, null)?.use { cursor ->
                val nameIndex = cursor.getColumnIndex(OpenableColumns.DISPLAY_NAME)
                val sizeIndex = cursor.getColumnIndex(OpenableColumns.SIZE)
                if (cursor.moveToFirst()) {
                    if (nameIndex >= 0) name = cursor.getString(nameIndex) ?: name
                    if (sizeIndex >= 0) size = cursor.getLong(sizeIndex)
                }
            }
        }
        if (name.isBlank()) name = "文件"
        return name to size
    }

    /**
     * 把 URI 指向的文件复制到应用缓存目录。
     *
     * @param context 上下文
     * @param uri 文件 URI
     * @param displayName 原始文件名
     * @return 缓存中的文件
     */
    fun copyToCache(context: Context, uri: Uri, displayName: String): File {
        val dir = File(context.cacheDir, "incoming").apply { mkdirs() }
        val safeName = displayName.replace(Regex("[\\\\/:*?\"<>|]"), "_")
        val target = File(dir, "${System.currentTimeMillis()}_$safeName")
        context.contentResolver.openInputStream(uri).use { input ->
            requireNotNull(input) { "无法读取文件内容" }
            target.outputStream().use { output -> input.copyTo(output) }
        }
        return target
    }
}
