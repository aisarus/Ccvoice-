plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "com.voiceshell"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.voiceshell"
        minSdk = 26
        targetSdk = 34
        versionCode = 2
        versionName = "0.4.0"
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
    }

    // Постоянный ключ: иначе каждая сборка в CI подписывается новым
    // отладочным ключом, и Android отказывается ставить обновление поверх.
    //
    // Ключ в репозитории — это ключ, который есть у всех: чужой человек
    // соберёт APK, который встанет обновлением поверх настоящего. Поэтому
    // настоящий релиз подписывается ключом из секретов GitHub Actions, а
    // отладочный из репозитория остаётся запасным — иначе форк и локальная
    // сборка у того, кто секретов не заводил, перестали бы собираться вовсе.
    val releaseKeystore = (project.findProperty("keystore") as String?)
        ?: System.getenv("ANDROID_KEYSTORE_FILE")
    val releasePassword = System.getenv("ANDROID_KEYSTORE_PASSWORD")

    signingConfigs {
        create("shared") {
            if (!releaseKeystore.isNullOrBlank() && !releasePassword.isNullOrBlank()) {
                storeFile = file(releaseKeystore)
                storePassword = releasePassword
                keyAlias = "voiceshell"
                keyPassword = releasePassword
            } else {
                storeFile = file("voice-shell.keystore")
                storePassword = "voiceshell"
                keyAlias = "voiceshell"
                keyPassword = "voiceshell"
            }
        }
    }

    buildTypes {
        debug {
            signingConfig = signingConfigs.getByName("shared")
        }
        release {
            isMinifyEnabled = false
            signingConfig = signingConfigs.getByName("shared")
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.appcompat:appcompat:1.7.0")
    implementation("androidx.media:media:1.7.0")
    implementation("com.google.android.material:material:1.12.0")
    implementation("com.squareup.okhttp3:okhttp:4.12.0")
    implementation("com.alphacephei:vosk-android:0.3.47@aar")
    implementation("net.java.dev.jna:jna:5.13.0@aar")

    // Чистая логика проверяется на JVM: это секунды, без эмулятора.
    testImplementation("junit:junit:4.13.2")

    // Звук проверяется только на устройстве: системного AudioManager в JVM нет.
    androidTestImplementation("androidx.test.ext:junit:1.2.1")
    androidTestImplementation("androidx.test:runner:1.6.2")
    androidTestImplementation("androidx.test:rules:1.6.1")
}
