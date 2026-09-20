// Versions are deliberately conservative rather than latest: this project
// exists to answer a hardware question, and a toolchain argument is not
// the question.
plugins {
    id("com.android.application") version "8.7.3" apply false
    id("org.jetbrains.kotlin.android") version "1.9.25" apply false
}
