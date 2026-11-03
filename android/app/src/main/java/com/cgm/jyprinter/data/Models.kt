package com.cgm.jyprinter.data

import java.io.File

/**
 * 当前登录账号信息。
 *
 * @param username 账号
 * @param roleText 角色中文名
 */
data class MeInfo(
    val username: String,
    val roleText: String,
)

/**
 * 打印机条目。
 *
 * @param name 打印机名称（提交打印时使用）
 * @param isDefault 是否为默认打印机
 * @param status 状态文案
 */
data class Printer(
    val name: String,
    val isDefault: Boolean,
    val status: String,
)

/**
 * 打印机列表响应。
 *
 * @param printers 打印机列表
 * @param default 默认打印机名称
 */
data class PrintersResp(
    val printers: List<Printer>,
    val default: String,
)

/**
 * 单张纸张。
 *
 * @param id 纸张 ID（Linux 侧可能为空）
 * @param name 纸张名称（提交打印时使用）
 */
data class Paper(
    val id: String,
    val name: String,
)

/**
 * 打印机能力。
 *
 * @param papers 可选纸张
 * @param duplex 是否支持双面
 * @param probed 是否由驱动真实探测得到
 */
data class Caps(
    val papers: List<Paper>,
    val duplex: Boolean,
    val probed: Boolean,
)

/**
 * 待打印文件信息。
 *
 * @param file 已复制到应用缓存的本地文件
 * @param displayName 原始文件名
 * @param size 字节数
 * @param fromExternal 是否由其他应用（如微信）传入
 */
data class PendingFile(
    val file: File,
    val displayName: String,
    val size: Long,
    val fromExternal: Boolean,
) {
    /** 人类可读的大小文本 */
    val sizeText: String
        get() = when {
            size >= 1024 * 1024 -> String.format("%.1f MB", size / 1024.0 / 1024.0)
            size >= 1024 -> String.format("%.1f KB", size / 1024.0)
            else -> "$size B"
        }

    /** 文件扩展名（不含点） */
    val ext: String
        get() = displayName.substringAfterLast('.', "").lowercase()
}

/**
 * 上传结果。
 *
 * @param tasks 成功入队的任务数量
 * @param rejected 被拒绝的文件及原因
 */
data class UploadResult(
    val tasks: List<PrintTask>,
    val rejected: List<Pair<String, String>>,
)

/**
 * 打印任务。
 *
 * @param id 任务 ID
 * @param filename 文件名
 * @param status 内部状态（queued/printing/done/failed/canceled/review）
 * @param statusText 状态中文
 * @param sizeText 大小文本
 * @param paramsText 参数摘要
 * @param message 失败原因或补充说明
 * @param createdAt 创建时间戳
 */
data class PrintTask(
    val id: String,
    val filename: String,
    val status: String,
    val statusText: String,
    val sizeText: String,
    val paramsText: String,
    val message: String,
    val createdAt: Double,
)

/**
 * 单个统计窗口（今日 / 近 7 天 / 近 30 天 / 累计）。
 *
 * @param label 窗口名称
 * @param tasks 已完成任务数
 * @param copies 累计份数
 * @param pages 累计打印页数
 * @param sheets 估算用纸张数
 */
data class StatBucket(
    val label: String,
    val tasks: Int,
    val copies: Int,
    val pages: Int,
    val sheets: Int,
)

/**
 * 打印量统计（服务端 `/api/stats`，仅管理员可见）。
 *
 * @param today 今日
 * @param week 近 7 天
 * @param month 近 30 天
 */
data class PrintStats(
    val today: StatBucket,
    val week: StatBucket,
    val month: StatBucket,
)

/**
 * 业务异常：携带可直接展示给用户的中文提示。
 *
 * @param text 提示文案
 */
class ApiException(message: String) : Exception(message)
