package com.cgm.jyprinter.data

import android.content.Context

/**
 * 本地配置与登录凭据的持久化存储。
 *
 * 说明：数据保存在应用私有目录的 SharedPreferences 中（其他应用无法读取），
 * 已在备份规则中排除，不会随系统备份或换机迁移带出。
 *
 * @param context 应用上下文
 */
class Prefs(context: Context) {

    private val sp = context.getSharedPreferences("jyprinter", Context.MODE_PRIVATE)

    /** 服务地址，形如 http://192.168.1.10:5000 */
    var serverUrl: String
        get() = sp.getString(KEY_SERVER, "").orEmpty()
        set(value) = sp.edit().putString(KEY_SERVER, value).apply()

    /** 登录账号 */
    var username: String
        get() = sp.getString(KEY_USER, "").orEmpty()
        set(value) = sp.edit().putString(KEY_USER, value).apply()

    /** 登录密码（仅在勾选「记住密码」时写入） */
    var password: String
        get() = sp.getString(KEY_PASSWORD, "").orEmpty()
        set(value) = sp.edit().putString(KEY_PASSWORD, value).apply()

    /** 是否自动登录 */
    var autoLogin: Boolean
        get() = sp.getBoolean(KEY_AUTO_LOGIN, true)
        set(value) = sp.edit().putBoolean(KEY_AUTO_LOGIN, value).apply()

    /** 会话 Cookie（由 CookieJar 维护） */
    var cookies: String
        get() = sp.getString(KEY_COOKIES, "").orEmpty()
        set(value) = sp.edit().putString(KEY_COOKIES, value).apply()

    /** 上次使用的打印机名称 */
    var lastPrinter: String
        get() = sp.getString(KEY_PRINTER, "").orEmpty()
        set(value) = sp.edit().putString(KEY_PRINTER, value).apply()

    /**
     * 清空会话 Cookie。
     *
     * @return 无
     */
    fun clearCookies() {
        sp.edit().remove(KEY_COOKIES).apply()
    }

    /**
     * 退出登录：保留服务地址与账号，清除密码与 Cookie。
     *
     * @return 无
     */
    fun clearSession() {
        sp.edit().remove(KEY_PASSWORD).remove(KEY_COOKIES).apply()
    }

    private companion object {
        const val KEY_SERVER = "server_url"
        const val KEY_USER = "username"
        const val KEY_PASSWORD = "password"
        const val KEY_AUTO_LOGIN = "auto_login"
        const val KEY_COOKIES = "cookies"
        const val KEY_PRINTER = "last_printer"
    }
}
