package com.cgm.jyprinter.data

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.Cookie
import okhttp3.CookieJar
import okhttp3.FormBody
import okhttp3.HttpUrl
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.MultipartBody
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.asRequestBody
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.io.IOException
import java.util.concurrent.TimeUnit

/**
 * 打印参数集合，字段与网页端 `/api/upload` 完全一致。
 *
 * @param printer 目标打印机（空表示默认打印机）
 * @param copies 份数
 * @param duplex simplex 单面 / vertical 双面
 * @param paper 纸张名称
 * @param pageRange 页码范围，如 1-3,5（空表示全部）
 * @param scale fit / actual / custom
 * @param scalePercent 自定义缩放百分比（10~400）
 * @param color color / mono
 * @param quality draft / normal / high
 */
data class PrintOptions(
    val printer: String,
    val copies: Int,
    val duplex: String,
    val paper: String,
    val pageRange: String,
    val scale: String,
    val scalePercent: Int,
    val color: String,
    val quality: String,
)

/**
 * 与网络打印服务通信的客户端。
 *
 * 采用表单登录 + 会话 Cookie 的方式对接服务端，Cookie 持久化在本地，
 * 因此登录一次后重启 APP 仍可复用会话（会话过期时返回 401 触发重新登录）。
 *
 * @param prefs 本地配置存储
 */
class ApiClient(private val prefs: Prefs) {

    private val client: OkHttpClient = OkHttpClient.Builder()
        .cookieJar(PersistentCookieJar(prefs))
        .connectTimeout(12, TimeUnit.SECONDS)
        .readTimeout(90, TimeUnit.SECONDS)
        .writeTimeout(180, TimeUnit.SECONDS)
        .build()

    /** 当前服务地址（不含末尾斜杠） */
    var baseUrl: String = ""
        private set

    /**
     * 设置服务地址，自动补全协议并去掉末尾斜杠。
     *
     * @param raw 用户输入的地址，如 192.168.1.10:5000
     * @return 规范化后的地址
     */
    fun setBaseUrl(raw: String): String {
        baseUrl = normalize(raw)
        return baseUrl
    }

    /**
     * 校验并规范化服务地址。
     *
     * @param raw 原始输入
     * @return 规范化地址（可能为空串）
     */
    fun normalize(raw: String): String {
        var text = raw.trim()
        if (text.isEmpty()) return ""
        if (!text.startsWith("http://", true) && !text.startsWith("https://", true)) {
            text = "http://$text"
        }
        return text.trimEnd('/')
    }

    /**
     * 登录并返回账号信息。
     *
     * @param username 账号
     * @param password 密码
     * @return 账号信息
     * @throws ApiException 账号密码错误或网络异常
     */
    suspend fun login(username: String, password: String): MeInfo = withContext(Dispatchers.IO) {
        if (baseUrl.isEmpty()) throw ApiException("请先填写服务地址")
        prefs.clearCookies()
        val form = FormBody.Builder()
            .add("username", username)
            .add("password", password)
            .add("next", "/")
            .build()
        val request = Request.Builder().url(endpoint("/login")).post(form).build()
        try {
            client.newCall(request).execute().use { resp ->
                if (resp.code == 401) throw ApiException("用户名或密码错误")
                if (!resp.isSuccessful) throw ApiException("登录失败（HTTP ${resp.code}）")
            }
        } catch (e: IOException) {
            throw ApiException(ioMessage(e))
        }
        me() ?: throw ApiException("登录失败，请检查服务地址是否正确")
    }

    /**
     * 查询当前登录账号；未登录返回 null。
     *
     * @return 账号信息或 null
     */
    suspend fun me(): MeInfo? = withContext(Dispatchers.IO) {
        if (baseUrl.isEmpty()) return@withContext null
        val request = Request.Builder().url(endpoint("/api/me")).get().build()
        try {
            client.newCall(request).execute().use { resp ->
                if (!resp.isSuccessful) return@withContext null
                val text = resp.body?.string().orEmpty()
                if (text.isBlank()) return@withContext null
                val json = JSONObject(text)
                if (!json.optBoolean("ok")) return@withContext null
                MeInfo(
                    username = json.optString("username"),
                    roleText = json.optString("role_text", "用户"),
                )
            }
        } catch (e: Exception) {
            null
        }
    }

    /**
     * 查询打印机列表。
     *
     * @return 打印机列表与默认打印机
     */
    suspend fun printers(): PrintersResp = withContext(Dispatchers.IO) {
        val json = JSONObject(get("/api/printers"))
        val array = json.optJSONArray("printers") ?: JSONArray()
        val list = ArrayList<Printer>(array.length())
        for (i in 0 until array.length()) {
            val item = array.optJSONObject(i) ?: continue
            list.add(
                Printer(
                    name = item.optString("name"),
                    isDefault = item.optBoolean("is_default"),
                    status = item.optString("status"),
                )
            )
        }
        PrintersResp(list, json.optString("default"))
    }

    /**
     * 查询指定打印机的纸张与双面能力。
     *
     * @param name 打印机名称
     * @return 能力信息
     */
    suspend fun capabilities(name: String): Caps = withContext(Dispatchers.IO) {
        val url = endpoint("/api/printer/capabilities").newBuilder()
            .addQueryParameter("name", name)
            .build()
        val json = JSONObject(get(url))
        val array = json.optJSONArray("papers") ?: JSONArray()
        val papers = ArrayList<Paper>(array.length())
        for (i in 0 until array.length()) {
            val item = array.optJSONObject(i) ?: continue
            papers.add(Paper(item.optString("id"), item.optString("name")))
        }
        Caps(
            papers = if (papers.isEmpty()) listOf(Paper("", "A4")) else papers,
            duplex = json.optBoolean("duplex", true),
            probed = json.optBoolean("probed", false),
        )
    }

    /**
     * 上传文件并提交打印任务。
     *
     * @param file 本地文件
     * @param displayName 原始文件名
     * @param options 打印参数
     * @return 上传结果
     */
    suspend fun upload(
        file: File,
        displayName: String,
        options: PrintOptions,
    ): UploadResult = withContext(Dispatchers.IO) {
        val body = MultipartBody.Builder()
            .setType(MultipartBody.FORM)
            .addFormDataPart("printer", options.printer)
            .addFormDataPart("copies", options.copies.toString())
            .addFormDataPart("duplex", options.duplex)
            .addFormDataPart("paper", options.paper)
            .addFormDataPart("page_range", options.pageRange)
            .addFormDataPart("scale", options.scale)
            .addFormDataPart("scale_percent", options.scalePercent.toString())
            .addFormDataPart("color", options.color)
            .addFormDataPart("quality", options.quality)
            .addFormDataPart(
                "files",
                displayName,
                file.asRequestBody("application/octet-stream".toMediaType()),
            )
            .build()
        val request = Request.Builder().url(endpoint("/api/upload")).post(body).build()
        val text = try {
            client.newCall(request).execute().use { resp ->
                val content = resp.body?.string().orEmpty()
                if (resp.code == 401) throw ApiException("登录已过期，请重新登录")
                if (!resp.isSuccessful) throw ApiException(parseError(content) ?: "上传失败（HTTP ${resp.code}）")
                content
            }
        } catch (e: IOException) {
            throw ApiException(ioMessage(e))
        }
        val json = JSONObject(text)
        val tasks = parseTasks(json.optJSONArray("tasks") ?: JSONArray())
        val rejected = ArrayList<Pair<String, String>>()
        val rejectedArray = json.optJSONArray("rejected") ?: JSONArray()
        for (i in 0 until rejectedArray.length()) {
            val item = rejectedArray.optJSONObject(i) ?: continue
            rejected.add(item.optString("filename") to item.optString("reason"))
        }
        if (tasks.isEmpty() && rejected.isEmpty()) {
            throw ApiException(parseError(text) ?: "服务器未返回打印任务")
        }
        UploadResult(tasks, rejected)
    }

    /**
     * 查询最近的打印任务。
     *
     * @param limit 条数上限
     * @return 任务列表
     */
    suspend fun tasks(limit: Int = 30): List<PrintTask> = withContext(Dispatchers.IO) {
        val url = endpoint("/api/tasks").newBuilder()
            .addQueryParameter("limit", limit.toString())
            .build()
        val json = JSONObject(get(url))
        parseTasks(json.optJSONArray("tasks") ?: JSONArray())
    }

    /**
     * 取消排队中的任务。
     *
     * @param id 任务 ID
     * @return 服务端提示
     */
    suspend fun cancel(id: String): String = action("/api/tasks/$id/cancel")

    /**
     * 重试失败的任务。
     *
     * @param id 任务 ID
     * @return 服务端提示
     */
    suspend fun retry(id: String): String = action("/api/tasks/$id/retry")

    /**
     * 执行返回 JSON 的写操作。
     *
     * @param path 接口路径
     * @return 服务端提示文案
     */
    private suspend fun action(path: String): String = withContext(Dispatchers.IO) {
        val body = "".toRequestBody(null)
        val request = Request.Builder().url(endpoint(path)).post(body).build()
        val text = try {
            client.newCall(request).execute().use { resp ->
                val content = resp.body?.string().orEmpty()
                if (resp.code == 401) throw ApiException("登录已过期，请重新登录")
                if (!resp.isSuccessful) throw ApiException(parseError(content) ?: "操作失败（HTTP ${resp.code}）")
                content
            }
        } catch (e: IOException) {
            throw ApiException(ioMessage(e))
        }
        JSONObject(text).optString("message", "操作成功")
    }

    /**
     * 发起 GET 请求并返回响应体。
     *
     * @param path 路径或完整 URL
     * @return 响应文本
     */
    private fun get(path: String): String = get(endpoint(path))

    /**
     * 发起 GET 请求并返回响应体。
     *
     * @param url 完整 URL
     * @return 响应文本
     */
    private fun get(url: HttpUrl): String {
        val request = Request.Builder().url(url).get().build()
        return try {
            client.newCall(request).execute().use { resp ->
                val text = resp.body?.string().orEmpty()
                if (resp.code == 401) throw ApiException("登录已过期，请重新登录")
                if (!resp.isSuccessful) throw ApiException(parseError(text) ?: "请求失败（HTTP ${resp.code}）")
                text
            }
        } catch (e: IOException) {
            throw ApiException(ioMessage(e))
        }
    }

    /**
     * 拼接完整地址。
     *
     * @param path 以 / 开头的接口路径
     * @return 完整 URL
     */
    private fun endpoint(path: String): HttpUrl = "$baseUrl$path".toHttpUrl()

    /**
     * 解析任务数组。
     *
     * @param array JSON 数组
     * @return 任务列表
     */
    private fun parseTasks(array: JSONArray): List<PrintTask> {
        val list = ArrayList<PrintTask>(array.length())
        for (i in 0 until array.length()) {
            val item = array.optJSONObject(i) ?: continue
            list.add(
                PrintTask(
                    id = item.optString("id"),
                    filename = item.optString("filename"),
                    status = item.optString("status"),
                    statusText = item.optString("status_text", item.optString("status")),
                    sizeText = item.optString("size_text"),
                    paramsText = item.optString("params_text"),
                    message = item.optString("message"),
                    createdAt = item.optDouble("created_at", 0.0),
                )
            )
        }
        return list
    }

    /**
     * 从错误响应中提取可展示的原因。
     *
     * @param body 响应文本
     * @return 文案或 null
     */
    private fun parseError(body: String): String? {
        if (body.isBlank()) return null
        return try {
            val json = JSONObject(body)
            val message = json.optString("error").ifBlank { json.optString("message") }
            message.ifBlank { null }
        } catch (e: Exception) {
            null
        }
    }

    /**
     * 把网络异常翻译成用户能看懂的中文。
     *
     * @param e IO 异常
     * @return 提示文案
     */
    private fun ioMessage(e: IOException): String {
        val detail = e.message.orEmpty()
        return when {
            detail.contains("Failed to connect", true) || detail.contains("Connection refused", true) ->
                "无法连接服务器，请检查地址与端口是否正确"
            detail.contains("timeout", true) -> "连接超时，请确认手机与打印服务在同一网络"
            detail.contains("UnknownHost", true) -> "地址无法解析，请检查服务地址"
            else -> "网络错误：$detail"
        }
    }
}

/**
 * 会话 Cookie 持久化：把 Cookie 序列化进 SharedPreferences，重启后仍保持登录态。
 *
 * @param prefs 本地配置存储
 */
private class PersistentCookieJar(private val prefs: Prefs) : CookieJar {

    /**
     * 保存响应中的 Cookie。
     *
     * @param url 请求地址
     * @param cookies 新 Cookie
     * @return 无
     */
    override fun saveFromResponse(url: HttpUrl, cookies: List<Cookie>) {
        if (cookies.isEmpty()) return
        val existing = prefs.cookies.lines().filter { it.isNotBlank() }.toMutableList()
        for (cookie in cookies) {
            existing.removeAll { line ->
                val parsed = runCatching { Cookie.parse(url, line) }.getOrNull()
                parsed?.name == cookie.name
            }
            existing.add(cookie.toString())
        }
        prefs.cookies = existing.joinToString("\n")
    }

    /**
     * 为请求装载 Cookie。
     *
     * @param url 请求地址
     * @return 匹配的 Cookie 列表
     */
    override fun loadForRequest(url: HttpUrl): List<Cookie> =
        prefs.cookies.lines()
            .filter { it.isNotBlank() }
            .mapNotNull { runCatching { Cookie.parse(url, it) }.getOrNull() }
            .filter { it.matches(url) }
}
