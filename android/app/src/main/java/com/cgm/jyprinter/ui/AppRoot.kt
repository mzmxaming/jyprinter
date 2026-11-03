package com.cgm.jyprinter.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.rounded.Print
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import com.cgm.jyprinter.ui.theme.BrandBlue
import com.cgm.jyprinter.vm.MainViewModel
import com.cgm.jyprinter.vm.UiState

/**
 * 应用根组件：按登录状态切换启动页、登录页与主界面，并统一处理一次性提示。
 *
 * @param state 界面状态
 * @param vm 状态持有者
 * @return 无
 */
@Composable
fun AppRoot(state: UiState, vm: MainViewModel) {
    val snackbar = remember { SnackbarHostState() }

    LaunchedEffect(state.message) {
        val message = state.message
        if (!message.isNullOrBlank()) {
            snackbar.showSnackbar(message)
            vm.consumeMessage()
        }
    }

    when {
        state.booting -> SplashScreen()
        !state.loggedIn -> LoginScreen(
            state = state,
            onLogin = { server, user, password, remember, autoLogin ->
                vm.login(server, user, password, remember, autoLogin)
            },
        )
        else -> HomeScreen(state = state, vm = vm, snackbar = snackbar)
    }
}

/**
 * 启动页：自动登录期间显示品牌与进度。
 *
 * @return 无
 */
@Composable
private fun SplashScreen() {
    Box(
        modifier = Modifier
            .fillMaxSize()
            .background(MaterialTheme.colorScheme.background),
        contentAlignment = Alignment.Center,
    ) {
        Column(horizontalAlignment = Alignment.CenterHorizontally) {
            Surface(
                shape = CircleShape,
                color = BrandBlue,
                modifier = Modifier.size(78.dp),
            ) {
                Box(contentAlignment = Alignment.Center) {
                    Icon(
                        imageVector = Icons.Rounded.Print,
                        contentDescription = null,
                        tint = Color.White,
                        modifier = Modifier.size(38.dp),
                    )
                }
            }
            Text(
                text = "网络打印服务",
                style = MaterialTheme.typography.titleLarge,
                modifier = Modifier.padding(top = 18.dp),
            )
            CircularProgressIndicator(
                modifier = Modifier
                    .padding(top = 22.dp)
                    .size(24.dp),
                strokeWidth = 2.dp,
            )
            Box(modifier = Modifier.height(8.dp))
        }
    }
}
