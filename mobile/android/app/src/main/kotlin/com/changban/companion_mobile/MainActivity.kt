package com.changban.companion_mobile

import io.flutter.embedding.android.FlutterFragmentActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel
import android.Manifest
import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.pm.PackageManager
import android.os.Build
import androidx.core.app.ActivityCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import cn.jiguang.api.JCoreInterface
import cn.jiguang.api.utils.JCollectionAuth
import cn.jpush.android.api.JPushInterface
import cn.jpush.android.data.JPushConfig
import cn.jpush.android.data.JPushCollectControl
import com.jiguang.jpush.JPushHelper
import com.huawei.agconnect.AGConnectInstance

class MainActivity : FlutterFragmentActivity() {
    private var permissionResult: MethodChannel.Result? = null
    private val notificationPermissionRequest = 46012

    override fun shouldHandleDeeplinking(): Boolean = false

    override fun configureFlutterEngine(engine: FlutterEngine) {
        super.configureFlutterEngine(engine)
        MethodChannel(engine.dartExecutor.binaryMessenger, "xiaoban/native-push").setMethodCallHandler { call, result ->
            when (call.method) {
                "notificationPermission" -> {
                    if (Build.VERSION.SDK_INT >= 33 && ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
                        if (call.argument<Boolean>("request") == true) {
                            if (permissionResult != null) result.error("busy", "通知授权正在进行", null)
                            else {
                                permissionResult = result
                                ActivityCompat.requestPermissions(this, arrayOf(Manifest.permission.POST_NOTIFICATIONS), notificationPermissionRequest)
                            }
                        } else result.success(false)
                    } else result.success(NotificationManagerCompat.from(this).areNotificationsEnabled())
                }
                "initialize" -> {
                    if (!NotificationManagerCompat.from(this).areNotificationsEnabled()) {
                        result.error("permission", "尚未允许通知", null)
                    } else try {
                        val appKey = call.argument<String>("app_key") ?: ""
                        if (!appKey.matches(Regex("^[A-Za-z0-9]{24}$"))) {
                            result.error("config", "推送应用标识无效", null)
                        } else {
                            val vendors = call.argument<Map<String, String>>("manufacturers") ?: emptyMap()
                            if (Build.VERSION.SDK_INT >= 26) {
                                val channel = NotificationChannel("xiaoban_care", "小伴主动关心", NotificationManager.IMPORTANCE_DEFAULT)
                                channel.description = "你已允许的小伴关心与回复通知"
                                getSystemService(NotificationManager::class.java).createNotificationChannel(channel)
                            }
                            JPushInterface.setDebugMode(false)
                            JCoreInterface.setWakeEnable(applicationContext, false)
                            JCollectionAuth.enableAutoWakeup(applicationContext, false)
                            JCollectionAuth.setAuth(applicationContext, true)
                            if (!vendors["huawei_app_id"].isNullOrBlank()) {
                                AGConnectInstance.initialize(applicationContext)
                            }
                            JPushInterface.setCollectControl(applicationContext, JPushCollectControl.Builder().imsi(false).mac(false).wifi(false).bssid(false).ssid(false).imei(false).cell(false).build())
                            JPushInterface.setSmartPushEnable(applicationContext, false)
                            JPushInterface.setGeofenceEnable(applicationContext, false)
                            JPushInterface.setLinkMergeEnable(applicationContext, false)
                            JPushInterface.setDataInsightsEnable(applicationContext, false)
                            val config = JPushConfig()
                            config.setjAppKey(appKey)
                            vendors["xiaomi_app_id"]?.takeIf { it.isNotBlank() }?.let { config.setXmAppId(it) }
                            vendors["xiaomi_app_key"]?.takeIf { it.isNotBlank() }?.let { config.setXmAppKey(it) }
                            vendors["oppo_app_key"]?.takeIf { it.isNotBlank() }?.let { config.setOppoAppKey(it) }
                            vendors["oppo_app_secret"]?.takeIf { it.isNotBlank() }?.let { config.setOppoAppSecret(it) }
                            JPushInterface.init(applicationContext, config)
                            JPushInterface.setNotificationCallBackEnable(applicationContext, true)
                            JPushInterface.setChannel(applicationContext, "xiaoban-free")
                            JPushInterface.resumePush(applicationContext)
                            // Official plugin is pinned to3.6.0. Its callback helper is
                            // attached by super.configureFlutterEngine before this call.
                            JPushHelper.getInstance().setDartIsReady(true)
                            JPushHelper.getInstance().dispatchNotification()
                            JPushHelper.getInstance().dispatchRid("")
                            val metadata = packageManager.getApplicationInfo(packageName, PackageManager.GET_META_DATA).metaData
                            val compiled = mapOf(
                                "huawei_app_id" to (metadata?.get("com.huawei.hms.client.appid")?.toString()?.removePrefix("appid=") ?: ""),
                                "vivo_app_id" to (metadata?.get("com.vivo.push.app_id")?.toString() ?: ""),
                                "vivo_app_key" to (metadata?.get("com.vivo.push.api_key")?.toString() ?: ""),
                                "honor_app_id" to (metadata?.get("com.hihonor.push.app_id")?.toString() ?: "")
                            )
                            val missing = compiled.keys.filter { !vendors[it].isNullOrBlank() && vendors[it] != compiled[it] }
                            result.success(mapOf("rebuild_required" to missing.isNotEmpty(), "requires_rebuild" to missing, "compiled_manufacturers" to compiled))
                        }
                    } catch (_: Exception) {
                        result.error("initialization", "设备推送初始化未完成", null)
                    }
                }
                "stop" -> {
                    JPushInterface.stopPush(applicationContext)
                    JCollectionAuth.setAuth(applicationContext, false)
                    result.success(null)
                }
                else -> result.notImplemented()
            }
        }
    }

    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode == notificationPermissionRequest) {
            permissionResult?.success(grantResults.isNotEmpty() && grantResults[0] == PackageManager.PERMISSION_GRANTED && NotificationManagerCompat.from(this).areNotificationsEnabled())
            permissionResult = null
        }
    }

    override fun onDestroy() {
        permissionResult?.error("closed", "通知授权已取消", null)
        permissionResult = null
        super.onDestroy()
    }
}
