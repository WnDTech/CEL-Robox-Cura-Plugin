; Test circles for Robox
; Preheat and wait
M104 S240
M140 S100
M109
M190
; Home all
G28 X
G28 Y
G28 Z
; Go to start position
G90
G0 X105 Y75 Z5
; Draw first circle
G1 Z0.2 F600
G2 X105 Y75 I10 J0 F1200
; Draw second circle
G1 Z0.4
G2 X105 Y75 I15 J0 F1200
; Draw third circle
G1 Z0.6
G2 X105 Y75 I20 J0 F1200
; Lift
G0 Z10
; Cooldown
M104 S0
M140 S0
M84
