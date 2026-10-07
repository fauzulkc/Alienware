"""x17tune - ambient-aware power/thermal auto-tuner for the Alienware x17 R2.

The firmware (Dell BIOS + EC) cannot be safely replaced on this machine, so
the "dynamic power" brain lives here, in the OS: it senses load and heat,
estimates the room temperature, classifies the workload and moves the levers
the platform does expose (AWCC thermal profiles, fan boost, CPU power/EPP,
GPU clock locks / undervolt profiles).
"""

__version__ = "0.1.0"
