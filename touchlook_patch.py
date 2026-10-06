#!/usr/bin/env python3
"""Adds touch-drag camera look to the Metroid Prime port (run from the repo root).
Every edit is an exact-text replacement; if the source has changed and an anchor
is missing, it is reported and the script exits non-zero."""
import sys

failures = []

if "nativeTouchLook" in open("android/app/src/main/java/org/metroidprime/port/TouchControlsView.java", encoding="utf-8").read():
    print("already patched, nothing to do")
    sys.exit(0)

def sub(path, old, new, name):
    try:
        s = open(path, encoding="utf-8").read()
    except OSError as e:
        failures.append(f"{name}: cannot open {path}: {e}")
        return
    if "TouchLook" in new and new in s:
        print(f"already applied: {name}")
        return
    if s.count(old) != 1:
        failures.append(f"{name}: anchor found {s.count(old)} times in {path}")
        return
    open(path, "w", encoding="utf-8").write(s.replace(old, new, 1))
    print(f"ok: {name}")

J = "android/app/src/main/java/org/metroidprime/port/TouchControlsView.java"
H = "platform/include/port_debug.h"
D = "platform/debug_ui.cpp"

sub(J, "    private static final int HIDE = 4;\n",
    "    private static final int HIDE = 4;\n    // Twin-stick only: a finger dragged in free space turns the camera (TouchLook).\n    private static final int LOOK = 5;\n", "java LOOK const")
sub(J, "    private static native boolean nativeTakePhysicalInput();\n",
    "    private static native boolean nativeTakePhysicalInput();\n    private static native void nativeTouchLook(float dx, float dy);\n", "java native decl")
sub(J, "    private int rightPointer = -1;\n    private boolean hidden;",
    "    private int rightPointer = -1;\n    private int lookPointer = -1;\n    private boolean hidden;", "java lookPointer field")
sub(J, """        drawStick(canvas, rightStickX(width, height), rightStickY(height),
                  rightStickRadius(height), rightPointer, colored ? GC_YELLOW : 0);
""", """        if (!twinStickMode) {
            drawStick(canvas, rightStickX(width, height), rightStickY(height),
                      rightStickRadius(height), rightPointer, colored ? GC_YELLOW : 0);
        }
""", "java hide right stick")
sub(J, """                if (target != null &&
                    (target.type == LEFT_STICK || target.type == RIGHT_STICK)) {
                    updateStick(target, event.getX(i), event.getY(i));
                }""", """                if (target == null) {
                    continue;
                }
                if (target.type == LEFT_STICK || target.type == RIGHT_STICK) {
                    updateStick(target, event.getX(i), event.getY(i));
                } else if (target.type == LOOK) {
                    // Relative drag, like a mouse: the delta since the last event.
                    float x = event.getX(i);
                    float y = event.getY(i);
                    nativeTouchLook(x - target.x, y - target.y);
                    target.x = x;
                    target.y = y;
                }""", "java move handling")
sub(J, "        leftPointer = -1;\n        rightPointer = -1;\n        invalidate();\n    }\n\n    // Android sends",
    "        leftPointer = -1;\n        rightPointer = -1;\n        lookPointer = -1;\n        invalidate();\n    }\n\n    // Android sends", "java releaseAll")
sub(J, """        if (pointerId == rightPointer) {
            rightPointer = -1;
        }
    }""", """        if (pointerId == rightPointer) {
            rightPointer = -1;
        }
        if (pointerId == lookPointer) {
            lookPointer = -1;
        }
    }""", "java releasePointer")
sub(J, """        if (target.type == HIDE) {
            return;
        }
        if (target.type == BUTTON) {
            releaseControl(target.id);""", """        if (target.type == HIDE || target.type == LOOK) {
            return;
        }
        if (target.type == BUTTON) {
            releaseControl(target.id);""", "java releaseTarget")
sub(J, """            leftPointer = pointerId;
            targets.put(pointerId, target);
            updateStick(target, x, y);
        } else if (x >= width * 0.38f""", """            leftPointer = pointerId;
            targets.put(pointerId, target);
            updateStick(target, x, y);
        } else if (twinStickMode) {
            // Touch look replaces the on-screen right stick. One finger looks.
            if (lookPointer == -1 && x >= width * 0.38f) {
                TouchTarget target = new TouchTarget(LOOK, 0);
                target.x = x;
                target.y = y;
                lookPointer = pointerId;
                targets.put(pointerId, target);
            }
        } else if (x >= width * 0.38f""", "java assignPointer")

sub(H, "void AddMouseDelta(float dx, float dy);",
    "void AddMouseDelta(float dx, float dy);\n// Touch-screen drag in pixels (right/down positive) from the Android overlay.\n// Thread-safe: called on the UI thread.\nvoid AddTouchLook(float dx, float dy);", "header decl")
sub(D, "float sGyroPendingX = 0.f;\nfloat sGyroPendingY = 0.f;\n",
    "float sGyroPendingX = 0.f;\nfloat sGyroPendingY = 0.f;\n// Touch-look travel: written by the Android UI thread, drained on the game thread.\nstd::atomic<float> sTouchPendingX{0.f};\nstd::atomic<float> sTouchPendingY{0.f};\ninline void AtomicAdd(std::atomic<float>& a, float v) {\n  float cur = a.load(std::memory_order_relaxed);\n  while (!a.compare_exchange_weak(cur, cur + v, std::memory_order_relaxed)) {\n  }\n}\n", "cpp accumulators")
sub(D, "void BeginFrameMouse() {\n  sMouseFrameX = sMousePendingX + sGyroPendingX;\n  sMouseFrameY = sMousePendingY + sGyroPendingY;",
    "void AddTouchLook(float dx, float dy) {\n  // Same gate gyro uses: aim only drives the camera under mouse aim or twin stick,\n  // and the debug overlay owns touches while it is open.\n  if (!(sMouseAim || sTwinStick) || Visible() || !std::isfinite(dx) || !std::isfinite(dy)) return;\n  AtomicAdd(sTouchPendingX, dx);\n  AtomicAdd(sTouchPendingY, dy);\n}\n\nvoid BeginFrameMouse() {\n  sMouseFrameX = sMousePendingX + sGyroPendingX + sTouchPendingX.exchange(0.f);\n  sMouseFrameY = sMousePendingY + sGyroPendingY + sTouchPendingY.exchange(0.f);", "cpp BeginFrameMouse")
sub(D, "  const float dx = sMousePendingX + sGyroPendingX + sStickAimVelX * ahead;\n  const float dy = sMousePendingY + sGyroPendingY + sStickAimVelY * ahead;",
    "  const float dx = sMousePendingX + sGyroPendingX + sTouchPendingX.load() + sStickAimVelX * ahead;\n  const float dy = sMousePendingY + sGyroPendingY + sTouchPendingY.load() + sStickAimVelY * ahead;", "cpp presented delta")
sub(D, """extern "C" JNIEXPORT jboolean JNICALL
Java_org_metroidprime_port_TouchControlsView_nativeTwinStick(""", """extern "C" JNIEXPORT void JNICALL
Java_org_metroidprime_port_TouchControlsView_nativeTouchLook(JNIEnv*, jclass, jfloat dx, jfloat dy) {
  PortDebug::AddTouchLook(dx, dy);
}

extern "C" JNIEXPORT jboolean JNICALL
Java_org_metroidprime_port_TouchControlsView_nativeTwinStick(""", "cpp JNI export")

if failures:
    print("\nPATCH FAILED - the source no longer matches:", file=sys.stderr)
    for f in failures:
        print("  " + f, file=sys.stderr)
    sys.exit(1)
print("all edits applied")
