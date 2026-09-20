// These versions are not a preference, they are a requirement handed down
// by RootEncoder 2.7.5, which is compiled against API 36. Every one of its
// seven modules fails the build against compileSdk 35 with "requires
// libraries and applications that depend on it to compile against version
// 36 or later". API 36 in turn needs AGP 8.9+, and AGP 8.13 needs Gradle
// 8.13+ and a Kotlin plugin new enough to know about it.
//
// The Kotlin version is pinned for a second, separate reason: JitPack built
// RootEncoder 2.7.5 with Kotlin 2.3, so its .kotlin_module metadata is
// version 2.3.0 and any older compiler rejects the whole dependency with
// "Module was compiled with an incompatible version of Kotlin". It drags
// kotlin-stdlib 2.3.21 in transitively, which is the tell.
//
// An earlier attempt pinned 8.7.3 / Kotlin 1.9.25 on the theory that older
// is safer for a spike, then 2.1.21. Neither compiled. Left as a note so
// nobody "simplifies" it back.
plugins {
    id("com.android.application") version "8.13.2" apply false
    id("org.jetbrains.kotlin.android") version "2.3.21" apply false
}
