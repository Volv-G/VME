import org.jetbrains.kotlin.gradle.dsl.JvmTarget

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "works.vme.streamer"
    // 36 is forced by RootEncoder 2.7.5; see the root build file.
    compileSdk = 36

    defaultConfig {
        applicationId = "works.vme.streamer"
        // UVCAndroid needs 21+, RootEncoder's extra sources 21+. 24 is the
        // floor for the audio-device routing this harness uses, and no
        // phone that can run a 1080p encoder is below it anyway.
        minSdk = 24
        targetSdk = 36
        versionCode = 1
        versionName = "0.1-spike"
    }

    buildTypes {
        release {
            isMinifyEnabled = false
        }
        debug {
            isMinifyEnabled = false
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }


    packaging {
        // UVCAndroid ships native libs for several ABIs; nothing here
        // conflicts, but the jni libs are the bulk of the APK.
        jniLibs {
            useLegacyPackaging = false
        }
    }
}

// kotlinOptions.jvmTarget is a hard error in Kotlin 2.3, not a warning.
kotlin {
    compilerOptions {
        jvmTarget.set(JvmTarget.JVM_17)
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.activity:activity:1.9.3")

    // The UVC stack. This is the thing being tested; see UvcVideoSource.
    implementation("com.herohan:UVCAndroid:1.0.13")

    // Encoder + RTMP/RTMPS. `extra-sources` is deliberately NOT included:
    // its CameraUvcSource is the 28-line default this spike replaces, and
    // pulling it in would only add a second, worse path to the same place.
    implementation("com.github.pedroSG94.RootEncoder:library:2.7.5")
}
