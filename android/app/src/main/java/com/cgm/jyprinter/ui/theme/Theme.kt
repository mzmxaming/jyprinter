package com.cgm.jyprinter.ui.theme

import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Shapes
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

/** 品牌主色：深墨蓝，配合青绿点缀，贴近打印/办公场景的稳重感 */
val BrandBlue = Color(0xFF16537E)
val BrandBlueDark = Color(0xFF0B3A5C)
val BrandTeal = Color(0xFF0F8B8D)
val BrandAmber = Color(0xFFE8A33D)

private val LightColors = lightColorScheme(
    primary = BrandBlue,
    onPrimary = Color.White,
    primaryContainer = Color(0xFFD6E7F5),
    onPrimaryContainer = Color(0xFF062F4B),
    secondary = BrandTeal,
    onSecondary = Color.White,
    secondaryContainer = Color(0xFFD3EFEF),
    onSecondaryContainer = Color(0xFF00343A),
    tertiary = BrandAmber,
    onTertiary = Color(0xFF3B2600),
    tertiaryContainer = Color(0xFFFFE7C2),
    onTertiaryContainer = Color(0xFF2E1E00),
    background = Color(0xFFF5F7FA),
    onBackground = Color(0xFF16202A),
    surface = Color.White,
    onSurface = Color(0xFF16202A),
    surfaceVariant = Color(0xFFEDF2F7),
    onSurfaceVariant = Color(0xFF4A5A68),
    outline = Color(0xFFB8C4D0),
    outlineVariant = Color(0xFFDCE4EC),
    error = Color(0xFFC0392B),
    errorContainer = Color(0xFFFBE4E1),
)

private val DarkColors = darkColorScheme(
    primary = Color(0xFF8CC6EC),
    onPrimary = Color(0xFF002F4A),
    primaryContainer = Color(0xFF16537E),
    onPrimaryContainer = Color(0xFFD6E7F5),
    secondary = Color(0xFF7FD6D8),
    onSecondary = Color(0xFF00363A),
    secondaryContainer = Color(0xFF07575A),
    onSecondaryContainer = Color(0xFFD3EFEF),
    background = Color(0xFF0F1620),
    onBackground = Color(0xFFE3E9EF),
    surface = Color(0xFF17212B),
    onSurface = Color(0xFFE3E9EF),
    surfaceVariant = Color(0xFF223040),
    onSurfaceVariant = Color(0xFFB3C1CD),
    outline = Color(0xFF54697C),
)

private val AppTypography = Typography(
    headlineSmall = TextStyle(fontSize = 22.sp, fontWeight = FontWeight.SemiBold, lineHeight = 30.sp),
    titleLarge = TextStyle(fontSize = 19.sp, fontWeight = FontWeight.SemiBold, lineHeight = 26.sp),
    titleMedium = TextStyle(fontSize = 16.sp, fontWeight = FontWeight.Medium, lineHeight = 22.sp),
    bodyLarge = TextStyle(fontSize = 16.sp, lineHeight = 24.sp),
    bodyMedium = TextStyle(fontSize = 14.sp, lineHeight = 20.sp),
    labelLarge = TextStyle(fontSize = 14.sp, fontWeight = FontWeight.Medium),
    labelMedium = TextStyle(fontSize = 12.sp, fontWeight = FontWeight.Medium),
)

private val AppShapes = Shapes(
    extraSmall = RoundedCornerShape(8.dp),
    small = RoundedCornerShape(12.dp),
    medium = RoundedCornerShape(16.dp),
    large = RoundedCornerShape(22.dp),
    extraLarge = RoundedCornerShape(28.dp),
)

/**
 * 应用主题：统一配色、字体与圆角，固定浅色/深色两套方案（不启用动态取色，
 * 保证在国产 ROM 上品牌色一致）。
 *
 * @param darkTheme 是否使用深色主题
 * @param content 内容
 * @return 无
 */
@Composable
fun JyPrinterTheme(
    darkTheme: Boolean = androidx.compose.foundation.isSystemInDarkTheme(),
    content: @Composable () -> Unit,
) {
    MaterialTheme(
        colorScheme = if (darkTheme) DarkColors else LightColors,
        typography = AppTypography,
        shapes = AppShapes,
        content = content,
    )
}
