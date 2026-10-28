pluginManagement {
    repositories {
        google()
        mavenCentral()
        gradlePluginPortal()
    }
}

dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        google()
        mavenCentral()
        // RootEncoder publishes through JitPack only.
        maven { url = uri("https://jitpack.io") }
    }
    // AGP, Kotlin, SDK levels and the common libraries, shared by every
    // app on the kit (android\kit is the Volv-G/android-kit submodule).
    versionCatalogs {
        create("kit") { from(files("kit/versions.toml")) }
    }
}

rootProject.name = "vme-streamer-spike"
include(":app")
