# Struktur
```mermaid
graph LR
    %% Zentrales Gateway
    VR921((<b>Vaillant VR921</b><br/>EEBUS Gateway))

    %% Tier 2 & 3 Cluster: KOMPRESSOR
    subgraph E31 [Entity 3,1: Compressor]
        direction TB
        F11C[f=11: Measurement]
        F19[f=19: SmartEnergy]
        F7[f=7: Electrical]
        
        op31{Operations}
        op31 --- op31_1[READ: Power/Energy]
        op31 --- op31_2[SUB: Energy Management]
    end

    %% Tier 2 & 3 Cluster: WARMWASSER
    subgraph E4 [Entity 4: DHW Circuit]
        direction TB
        F11W[f=11: Measurement]
        F18W[f=18: Setpoint]
        
        op4{Operations}
        op4 --- op4_1[READ: Ist-Temp]
        op4 --- op4_2[READ: Setpoint List]
        op4 --- op4_3[READ: Constraints 35-70°C]
        op4 --- op4_4[SUB: Active Monitoring]
    end

    %% Tier 2 & 3 Cluster: HEIZKREIS
    subgraph E5 [Entity 5,1,1: HVAC Room]
        direction TB
        F11R[f=11: Measurement]
        F18R[f=18: Setpoint]
        
        op5{Operations}
        op5 --- op5_1[READ: Raum-Temp]
        op5 --- op5_2[READ: Setpoint List]
        op5 --- op5_3[READ: Constraints 5-30°C]
        op5 --- op5_4[SUB: Active Monitoring]
    end

    %% Tier 2 & 3 Cluster: AUSSENFÜHLER
    subgraph E6 [Entity 6: Temp Sensor]
        direction TB
        F11A[f=11: Measurement]
        
        op6{Operations}
        op6 --- op6_1[SKIP: Read]
        op6 --- op6_2[MODE: Notify-Only]
    end

    %% Physische Verbindungen
    VR921 ==> E31
    VR921 ==> E4
    VR921 ==> E5
    VR921 ==> E6

    %% Styling
    style VR921 fill:#f9f,stroke:#333,stroke-width:4px
    style E31 fill:#e1f5fe,stroke:#01579b
    style E4 fill:#fff3e0,stroke:#e65100
    style E5 fill:#f1f8e9,stroke:#33691e
    style E6 fill:#eceff1,stroke:#455a64
    
    style op6_2 fill:#ffcdd2,stroke:#b71c1c
    style op4_4 fill:#c8e6c9,stroke:#2e7d32
    style op5_4 fill:#c8e6c9,stroke:#2e7d32
