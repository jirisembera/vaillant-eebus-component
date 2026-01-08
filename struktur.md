# Struktur
```mermaid
graph TD
    %% Tier 1: Device
    Device[<b>Tier 1: Device</b><br/>Vaillant VR921 Gateway<br/>ID: 212232...6209] 

    %% Tier 2: Entities
    subgraph Entities [<b>Tier 2: Entities</b>]
        E0[entity=0<br/>Device Information]
        E3[entity=3<br/>HeatPump Appliance]
        E31[entity=3,1<br/>Compressor]
        E4[entity=4<br/>DHW Circuit<br/>Warmwasser]
        E511[entity=5,1,1<br/>HVAC Room<br/>Heizkreis]
        E6[entity=6<br/>Temp Sensor<br/>Außenfühler]
    end

    %% Tier 3: Features
    subgraph Features [<b>Tier 3: Features</b>]
        F11_C[feature=11<br/>Measurement<br/>Power/Energy]
        F19[feature=19<br/>SmartEnergy<br/>PV-Optimization]
        F11_W[feature=11<br/>Measurement<br/>Ist-Temp]
        F18_W[feature=18<br/>Setpoint<br/>Soll-Temp]
        F11_R[feature=11<br/>Measurement<br/>Zimmer-Temp]
        F18_R[feature=18<br/>Setpoint<br/>Soll-Temp]
        F11_A[feature=11<br/>Measurement<br/>Außen-Temp]
    end

    %% Verbindungen
    Device --> E0
    Device --> E3
    E3 --> E31
    Device --> E4
    Device --> E511
    Device --> E6

    E31 --> F11_C
    E31 --> F19
    E4 --> F11_W
    E4 --> F18_W
    E511 --> F11_R
    E511 --> F18_R
    E6 --> F11_A

    %% Styling
    style Device fill:#e9f,stroke:#333,stroke-width:2px
    style Entities fill:#fff,stroke:#333,stroke-dasharray: 5 5
    style Features fill:#dfd,stroke:#333,stroke-width:1px
