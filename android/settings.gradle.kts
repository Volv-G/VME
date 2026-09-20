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
}

rootProject.name = "vme-streamer-spike"
include(":app")
