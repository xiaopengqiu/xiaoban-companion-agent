plugins {
    id("com.android.application")
    // The Flutter Gradle Plugin must be applied after the Android and Kotlin Gradle plugins.
    id("dev.flutter.flutter-gradle-plugin")
}

android {
    namespace = "com.changban.companion_mobile"
    compileSdk = flutter.compileSdkVersion
    ndkVersion = flutter.ndkVersion

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    defaultConfig {
        // TODO: Specify your own unique Application ID (https://developer.android.com/studio/build/application-id.html).
        applicationId = "com.changban.companion_mobile"
        // You can update the following values to match your application needs.
        // For more information, see: https://flutter.dev/to/review-gradle-config.
        minSdk = 29
        targetSdk = flutter.targetSdkVersion
        // Uses the version code from pubspec.yaml. When using split APKs, 1000 * ABI_VERSION
        // is added automatically by Flutter. (https://developer.android.com/studio/build/configure-apk-splits#configure-APK-versions)
        // You can force using the value of versionCode by specifying the `-P force-version-code-ignoring-abi=true`
        // flag during build.
        versionCode = flutter.versionCode
        versionName = flutter.versionName
        // Public SDK client identifiers only. Server-side MasterSecret is never
        // accepted as a build parameter or embedded in this application.
        manifestPlaceholders["JPUSH_PKGNAME"] = applicationId!!
        manifestPlaceholders["JPUSH_APPKEY"] = ""
        manifestPlaceholders["JPUSH_CHANNEL"] = "xiaoban-free"
        manifestPlaceholders["XIAOMI_APPID"] = ""
        manifestPlaceholders["XIAOMI_APPKEY"] = ""
        manifestPlaceholders["OPPO_APPID"] = ""
        manifestPlaceholders["OPPO_APPKEY"] = ""
        manifestPlaceholders["OPPO_APPSECRET"] = ""
        val huawei = providers.gradleProperty("xiaobanHuaweiAppId").orElse("").get()
        manifestPlaceholders["HUAWEI_APPID"] = if (huawei.isBlank()) "" else "appid=$huawei"
        manifestPlaceholders["VIVO_APPID"] = providers.gradleProperty("xiaobanVivoAppId").orElse("").get()
        manifestPlaceholders["VIVO_APPKEY"] = providers.gradleProperty("xiaobanVivoAppKey").orElse("").get()
        manifestPlaceholders["HONOR_APPID"] = providers.gradleProperty("xiaobanHonorAppId").orElse("").get()
    }

    buildTypes {
        release {
            // TODO: Add your own signing config for the release build.
            // Signing with the debug keys for now, so `flutter run --release` works.
            signingConfig = signingConfigs.getByName("debug")
        }
    }
}

dependencies {
    implementation("cn.jiguang.sdk:jpush:6.3.0")
    implementation("cn.jiguang.sdk:jcore:5.6.0")
    implementation("cn.jiguang.sdk.plugin:huawei:6.3.0")
    implementation("cn.jiguang.sdk.plugin:xiaomi:6.3.0")
    implementation("cn.jiguang.sdk.plugin:oppo:6.3.0")
    implementation("cn.jiguang.sdk.plugin:vivo:6.3.0")
    implementation("cn.jiguang.sdk.plugin:honor:6.3.0")
    // Reuse FlutterFire's resolved Firebase SDK instead of packaging an embedded
    // second copy from the vendor extension.
    implementation("cn.jiguang.sdk.plugin:fcm:6.3.0") {
        exclude(group = "cn.jiguang.sdk.plugin", module = "fcm_th_push")
    }
}

kotlin {
    compilerOptions {
        jvmTarget = org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17
    }
}

flutter {
    source = "../.."
}

// Firebase is optional: a normal local build needs no Firebase project.
if (file("google-services.json").exists()) {
    apply(plugin = "com.google.gms.google-services")
}
