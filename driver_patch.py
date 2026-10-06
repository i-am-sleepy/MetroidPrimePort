#!/usr/bin/env python3
"""Adds custom Vulkan (Turnip) driver loading to the Android build.
Run from the repo root, after touchlook_patch.py (independent of it)."""
import os, sys

CPP = r'''// Custom Vulkan driver (Turnip) support for Android.
//
// Dawn opens the system Vulkan loader with dlopen("libvulkan.so"). The build
// links with -Wl,--wrap=dlopen, so that call lands here. If a driver file is
// found, it is loaded through libadrenotools, which returns a Vulkan loader that
// talks to that driver; Dawn gets it in place of the system loader. With no
// driver, or on any failure, the call falls through to the real dlopen.
//
// Put the driver's vulkan.*.so file (pulled out of the driver zip) in one of:
//   /storage/emulated/0/Android/data/org.metroidprime.port/files/custom_driver/
//   /storage/emulated/0/MetroidPrimePort/custom_driver/
// status.txt in the first folder says what happened on the last launch.
#include <android/log.h>
#include <dirent.h>
#include <dlfcn.h>
#include <sys/stat.h>
#include <unistd.h>

#include <adrenotools/driver.h>

#include <cstdio>
#include <cstring>
#include <mutex>
#include <string>

extern "C" void* __real_dlopen(const char* name, int flags);

namespace {

constexpr const char* kTag = "MPDriver";
constexpr const char* kPackage = "org.metroidprime.port";

std::mutex sMutex;
bool sTried = false;
void* sHandle = nullptr;
thread_local bool sInside = false;
std::string sStatusPath;

void Status(const std::string& msg) {
  __android_log_print(ANDROID_LOG_INFO, kTag, "%s", msg.c_str());
  if (!sStatusPath.empty()) {
    if (FILE* f = fopen(sStatusPath.c_str(), "w")) {
      fputs(msg.c_str(), f);
      fputc('\n', f);
      fclose(f);
    }
  }
}

void MkdirP(const std::string& path) {
  for (size_t i = 1; i < path.size(); ++i) {
    if (path[i] == '/') {
      mkdir(path.substr(0, i).c_str(), 0771);
    }
  }
  mkdir(path.c_str(), 0771);
}

bool EndsWith(const std::string& s, const char* suffix) {
  const size_t n = strlen(suffix);
  return s.size() >= n && s.compare(s.size() - n, n, suffix) == 0;
}

// The first *.so in the folder, preferring names that start with "vulkan.".
std::string FindDriver(const std::string& dir) {
  DIR* d = opendir(dir.c_str());
  if (!d) {
    return {};
  }
  std::string found;
  while (dirent* e = readdir(d)) {
    const std::string n = e->d_name;
    if (!EndsWith(n, ".so")) {
      continue;
    }
    if (n.rfind("vulkan.", 0) == 0 || found.empty()) {
      found = n;
    }
  }
  closedir(d);
  return found;
}

long FileSize(const std::string& p) {
  struct stat st;
  return stat(p.c_str(), &st) == 0 && S_ISREG(st.st_mode) ? static_cast<long>(st.st_size) : -1;
}

bool CopyFile(const std::string& from, const std::string& to) {
  FILE* in = fopen(from.c_str(), "rb");
  if (!in) {
    return false;
  }
  unlink(to.c_str());
  FILE* out = fopen(to.c_str(), "wb");
  if (!out) {
    fclose(in);
    return false;
  }
  char buf[1 << 16];
  size_t n;
  bool ok = true;
  while ((n = fread(buf, 1, sizeof buf, in)) > 0) {
    if (fwrite(buf, 1, n, out) != n) {
      ok = false;
      break;
    }
  }
  fclose(in);
  if (fclose(out) != 0) {
    ok = false;
  }
  if (ok) {
    chmod(to.c_str(), 0444);  // Android 14 wants loaded code read-only
  } else {
    unlink(to.c_str());
  }
  return ok;
}

std::string OwnLibDir() {
  Dl_info info;
  if (dladdr(reinterpret_cast<void*>(&OwnLibDir), &info) && info.dli_fname) {
    std::string p = info.dli_fname;
    const size_t slash = p.rfind('/');
    if (slash != std::string::npos) {
      return p.substr(0, slash + 1);
    }
  }
  return {};
}

void* OpenCustom(int flags) {
  std::lock_guard<std::mutex> lock(sMutex);
  if (sTried) {
    return sHandle;
  }
  sTried = true;

  const std::string priv = std::string("/data/data/") + kPackage;
  const std::string privDir = priv + "/files/custom_driver/";
  const std::string tmpDir = priv + "/cache/adrenotools/";
  const std::string extDir =
      std::string("/storage/emulated/0/Android/data/") + kPackage + "/files/custom_driver/";
  const std::string altDir = "/storage/emulated/0/MetroidPrimePort/custom_driver/";
  MkdirP(privDir);
  MkdirP(tmpDir);
  MkdirP(extDir);
  sStatusPath = extDir + "status.txt";

  std::string name;
  std::string srcDir;
  for (const std::string* dir : {&extDir, &altDir, &privDir}) {
    name = FindDriver(*dir);
    if (!name.empty()) {
      srcDir = *dir;
      break;
    }
  }
  if (name.empty()) {
    Status("No custom driver found, using the system Vulkan driver.\n"
           "Put the driver's vulkan.*.so file in:\n  " + extDir + "\nor:\n  " + altDir);
    return nullptr;
  }

  // Shared storage is mounted noexec, so the driver runs from a private copy.
  if (srcDir != privDir) {
    const long want = FileSize(srcDir + name);
    if (FileSize(privDir + name) != want && !CopyFile(srcDir + name, privDir + name)) {
      Status("Found " + name + " but could not copy it into the app's private folder.");
      return nullptr;
    }
  }

  const std::string hookDir = OwnLibDir();
  if (hookDir.empty() || FileSize(hookDir + "libhook_impl.so") < 0) {
    Status("Found " + name + " but the adrenotools hook libraries are missing from the app (" +
           hookDir + "). Using the system Vulkan driver.");
    return nullptr;
  }

  void* handle = adrenotools_open_libvulkan(flags, ADRENOTOOLS_DRIVER_CUSTOM, tmpDir.c_str(),
                                            hookDir.c_str(), privDir.c_str(), name.c_str(),
                                            nullptr, nullptr);
  if (!handle) {
    Status("Found " + name + " but libadrenotools could not load it (wrong driver build for "
           "this GPU?). Using the system Vulkan driver.");
    return nullptr;
  }
  sHandle = handle;
  Status("Loaded custom Vulkan driver: " + name);
  return handle;
}

}  // namespace

extern "C" __attribute__((visibility("default"))) void* __wrap_dlopen(const char* name, int flags) {
  if (!sInside && name != nullptr && strncmp(name, "libvulkan.so", 12) == 0) {
    sInside = true;  // libadrenotools may dlopen things itself
    void* handle = OpenCustom(flags);
    sInside = false;
    if (handle) {
      return handle;
    }
  }
  return __real_dlopen(name, flags);
}
'''

CMAKE = "CMakeLists.txt"
anchor = "    target_link_libraries(metroid_prime_port PRIVATE mediandk)\n"
block = anchor + """    # Custom Vulkan driver (Turnip) support: libadrenotools loads a user-supplied
    # Adreno driver, and port_custom_driver.cpp hands it to Dawn through
    # --wrap=dlopen in place of the system loader.
    include(FetchContent)
    FetchContent_Declare(adrenotools
        GIT_REPOSITORY https://github.com/bylaws/libadrenotools.git
        GIT_TAG master)
    FetchContent_MakeAvailable(adrenotools)
    target_sources(metroid_prime_port PRIVATE platform/port_custom_driver.cpp)
    target_link_libraries(metroid_prime_port PRIVATE adrenotools)
    target_link_options(metroid_prime_port PRIVATE -Wl,--wrap=dlopen)
    foreach(_mp_hook hook_impl main_hook file_redirect_hook gsl_alloc_hook)
        if(TARGET ${_mp_hook})
            add_dependencies(metroid_prime_port ${_mp_hook})
        endif()
    endforeach()
"""

s = open(CMAKE, encoding="utf-8").read()
if "port_custom_driver.cpp" in s:
    print("already patched, nothing to do")
    sys.exit(0)
if s.count(anchor) != 1:
    print(f"PATCH FAILED - CMake anchor found {s.count(anchor)} times in {CMAKE}", file=sys.stderr)
    sys.exit(1)
open(CMAKE, "w", encoding="utf-8").write(s.replace(anchor, block, 1))
os.makedirs("platform", exist_ok=True)
open("platform/port_custom_driver.cpp", "w", encoding="utf-8").write(CPP)
print("custom driver support added")
