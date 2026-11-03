package com.cgm.jyprinter.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.rounded.Add
import androidx.compose.material.icons.rounded.Article
import androidx.compose.material.icons.rounded.Image
import androidx.compose.material.icons.rounded.InsertDriveFile
import androidx.compose.material.icons.rounded.PictureAsPdf
import androidx.compose.material.icons.rounded.Remove
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ExposedDropdownMenuBox
import androidx.compose.material3.ExposedDropdownMenuDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.SegmentedButton
import androidx.compose.material3.SegmentedButtonDefaults
import androidx.compose.material3.SingleChoiceSegmentedButtonRow
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.cgm.jyprinter.data.Paper
import com.cgm.jyprinter.ui.theme.BrandBlue
import com.cgm.jyprinter.ui.theme.BrandBlueDark
import com.cgm.jyprinter.ui.theme.BrandTeal

/**
 * 通用卡片容器：统一圆角、内边距与标题排版。
 *
 * @param title 标题
 * @param subtitle 副标题（可选）
 * @param trailing 标题右侧内容（可选）
 * @param content 卡片内容
 * @return 无
 */
@Composable
fun SectionCard(
    title: String,
    subtitle: String? = null,
    trailing: (@Composable () -> Unit)? = null,
    content: @Composable () -> Unit,
) {
    Surface(
        modifier = Modifier.fillMaxWidth(),
        shape = RoundedCornerShape(22.dp),
        color = MaterialTheme.colorScheme.surface,
        tonalElevation = 0.dp,
        shadowElevation = 1.dp,
    ) {
        Column(modifier = Modifier.padding(18.dp)) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Column(modifier = Modifier.weight(1f)) {
                    Text(
                        text = title,
                        style = MaterialTheme.typography.titleMedium,
                        fontWeight = FontWeight.SemiBold,
                        color = MaterialTheme.colorScheme.onSurface,
                    )
                    if (!subtitle.isNullOrBlank()) {
                        Text(
                            text = subtitle,
                            style = MaterialTheme.typography.labelMedium,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                            modifier = Modifier.padding(top = 3.dp),
                        )
                    }
                }
                trailing?.invoke()
            }
            Column(modifier = Modifier.padding(top = 14.dp)) { content() }
        }
    }
}

/**
 * 顶部品牌渐变卡片，用于展示服务状态与账号。
 *
 * @param title 主标题
 * @param subtitle 副标题
 * @param trailing 右侧内容
 * @return 无
 */
@Composable
fun GradientHeader(
    title: String,
    subtitle: String,
    trailing: (@Composable () -> Unit)? = null,
) {
    Box(
        modifier = Modifier
            .fillMaxWidth()
            .background(
                brush = Brush.linearGradient(listOf(BrandBlueDark, BrandBlue, BrandTeal)),
                shape = RoundedCornerShape(22.dp),
            )
            .padding(horizontal = 18.dp, vertical = 16.dp),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(modifier = Modifier.weight(1f)) {
                Text(
                    text = title,
                    style = MaterialTheme.typography.titleLarge,
                    color = Color.White,
                )
                Text(
                    text = subtitle,
                    style = MaterialTheme.typography.labelMedium,
                    color = Color.White.copy(alpha = 0.82f),
                    modifier = Modifier.padding(top = 4.dp),
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
            }
            trailing?.invoke()
        }
    }
}

/**
 * 等宽分段选择器（单双面、缩放、颜色、质量共用）。
 *
 * @param options 选项列表，元素为「值 to 文案」
 * @param selected 当前值
 * @param enabled 是否可用
 * @param onSelect 选择回调
 * @return 无
 */
@Composable
fun SegmentedRow(
    options: List<Pair<String, String>>,
    selected: String,
    enabled: Boolean = true,
    onSelect: (String) -> Unit,
) {
    SingleChoiceSegmentedButtonRow(modifier = Modifier.fillMaxWidth()) {
        options.forEachIndexed { index, option ->
            SegmentedButton(
                selected = selected == option.first,
                onClick = { onSelect(option.first) },
                enabled = enabled,
                shape = SegmentedButtonDefaults.itemShape(index = index, count = options.size),
                colors = SegmentedButtonDefaults.colors(
                    activeContainerColor = MaterialTheme.colorScheme.primaryContainer,
                    activeContentColor = MaterialTheme.colorScheme.onPrimaryContainer,
                ),
            ) {
                Text(text = option.second, maxLines = 1, overflow = TextOverflow.Ellipsis)
            }
        }
    }
}

/**
 * 下拉选择框。
 *
 * @param label 标签
 * @param value 当前值
 * @param options 选项列表，元素为「值 to 文案」
 * @param onSelect 选择回调
 * @return 无
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun LabeledDropdown(
    label: String,
    value: String,
    options: List<Pair<String, String>>,
    onSelect: (String) -> Unit,
) {
    var expanded by remember { mutableStateOf(false) }
    val current = options.firstOrNull { it.first == value }?.second ?: value
    ExposedDropdownMenuBox(expanded = expanded, onExpandedChange = { expanded = it }) {
        OutlinedTextField(
            value = current,
            onValueChange = {},
            readOnly = true,
            label = { Text(label) },
            singleLine = true,
            shape = RoundedCornerShape(14.dp),
            trailingIcon = { ExposedDropdownMenuDefaults.TrailingIcon(expanded = expanded) },
            modifier = Modifier
                .fillMaxWidth()
                .menuAnchor(),
        )
        ExposedDropdownMenu(expanded = expanded, onDismissRequest = { expanded = false }) {
            options.forEach { option ->
                DropdownMenuItem(
                    text = { Text(option.second) },
                    onClick = {
                        onSelect(option.first)
                        expanded = false
                    },
                )
            }
        }
    }
}

/**
 * 份数步进器。
 *
 * @param value 当前份数
 * @param onDelta 增减回调
 * @return 无
 */
@Composable
fun CopiesStepper(value: Int, onDelta: (Int) -> Unit) {
    Surface(
        shape = RoundedCornerShape(14.dp),
        color = MaterialTheme.colorScheme.surfaceVariant,
    ) {
        Row(
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.Center,
        ) {
            IconButton(onClick = { onDelta(-1) }, enabled = value > 1) {
                Icon(Icons.Rounded.Remove, contentDescription = "减少份数")
            }
            Text(
                text = "$value 份",
                style = MaterialTheme.typography.titleMedium,
                modifier = Modifier.padding(horizontal = 6.dp),
            )
            IconButton(onClick = { onDelta(1) }, enabled = value < 99) {
                Icon(Icons.Rounded.Add, contentDescription = "增加份数")
            }
        }
    }
}

/**
 * 状态标签：按任务状态着色。
 *
 * @param status 内部状态
 * @param text 展示文案
 * @return 无
 */
@Composable
fun StatusPill(status: String, text: String) {
    val scheme = MaterialTheme.colorScheme
    val (background, foreground) = when (status) {
        "done" -> scheme.secondaryContainer to scheme.onSecondaryContainer
        "printing" -> scheme.primaryContainer to scheme.onPrimaryContainer
        "failed" -> scheme.errorContainer to scheme.error
        "canceled" -> scheme.surfaceVariant to scheme.onSurfaceVariant
        "review" -> scheme.tertiaryContainer to scheme.onTertiaryContainer
        else -> scheme.surfaceVariant to scheme.onSurfaceVariant
    }
    Surface(shape = RoundedCornerShape(50), color = background) {
        Text(
            text = text,
            style = MaterialTheme.typography.labelMedium,
            color = foreground,
            modifier = Modifier.padding(horizontal = 10.dp, vertical = 4.dp),
        )
    }
}

/**
 * 按扩展名返回文件图标与配色。
 *
 * @param ext 扩展名（不含点）
 * @return 图标与颜色
 */
@Composable
fun fileVisual(ext: String): Pair<ImageVector, Color> = when (ext) {
    "pdf" -> Icons.Rounded.PictureAsPdf to Color(0xFFD64545)
    "jpg", "jpeg", "png", "bmp", "gif", "tiff", "webp" ->
        Icons.Rounded.Image to BrandTeal
    "txt", "md", "log" -> Icons.Rounded.Article to Color(0xFF5B7A99)
    "doc", "docx", "wps" -> Icons.Rounded.InsertDriveFile to Color(0xFF2C6CB0)
    "xls", "xlsx", "et" -> Icons.Rounded.InsertDriveFile to Color(0xFF2E8B57)
    "ppt", "pptx", "dps" -> Icons.Rounded.InsertDriveFile to Color(0xFFC4642A)
    else -> Icons.Rounded.InsertDriveFile to MaterialTheme.colorScheme.onSurfaceVariant
}

/**
 * 选项说明文字。
 *
 * @param text 文案
 * @return 无
 */
@Composable
fun OptionLabel(text: String) {
    Text(
        text = text,
        style = MaterialTheme.typography.labelMedium,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
        modifier = Modifier.padding(bottom = 6.dp),
    )
}

/**
 * 纸张列表转换为下拉选项。
 *
 * @param papers 纸张列表
 * @return 「名称 to 名称」列表
 */
fun paperOptions(papers: List<Paper>): List<Pair<String, String>> {
    val names = papers.map { it.name }.distinct()
    return if (names.isEmpty()) listOf("A4" to "A4") else names.map { it to it }
}

/**
 * 文件图标底色方块。
 *
 * @param ext 扩展名
 * @return 无
 */
@Composable
fun FileBadge(ext: String) {
    val (icon, color) = fileVisual(ext)
    Box(
        modifier = Modifier
            .size(52.dp)
            .background(color.copy(alpha = 0.14f), RoundedCornerShape(16.dp)),
        contentAlignment = Alignment.Center,
    ) {
        Icon(icon, contentDescription = null, tint = color, modifier = Modifier.size(26.dp))
    }
}
