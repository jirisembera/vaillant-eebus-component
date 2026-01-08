# Struktur
```mermaid
graph TD
    %% Tier 1
    T1[<b>Tier 1: Device</b><br/>Vaillant VR921 Gateway] 

    %% Tier 2
    subgraph T2 [Tier 2: Entities]
        E31[entity=3,1: Compressor]
        E4[entity=4: DHW Circuit]
        E5[entity=5,1,1: HVAC Room]
        E6[entity=6: Temp Sensor]
    end

    %% Tier 3
    subgraph T3 [Tier 3: Features]
        F11[feature=11: Measurement]
        F18[feature=18: Setpoint]
        F19[feature=19: SmartEnergy]
        F7[feature=7: Electrical]
    end

    %% Verbindungen
    T1 --> E31
    T1 --> E4
    T1 --> E5
    T1 --> E6

    E31 --> F11
    E31 --> F19
    E31 --> F7
    
    E4 --> F11
    E4 --> F18
    
    E5 --> F11
    E5 --> F18
    
    E6 --> F11

    %% Styling
    style T1 fill:#f9f,stroke:#333,stroke-width:2px
    style T2 fill:#fff,stroke:#333,stroke-dasharray: 5 5
    style T3 fill:#dfd,stroke:#333
