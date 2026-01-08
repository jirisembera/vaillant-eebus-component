```mermaid

graph TD
    %% Tier 1
    T1[<b>Tier 1: Device</b><br/>Vaillant VR921 Gateway] 

    %% Tier 2
    subgraph Entities [Tier 2: Entities]
        E3[entity=3: HeatPump]
        E4[entity=4: Warmwasser]
        E5[entity=5,1,1: Heizkreis]
        E6[entity=6: Außensensor]
    end

    %% Tier 3
    subgraph Features [Tier 3: Features]
        F11[feature=11: Measurement]
        F18[feature=18: Setpoint]
        F19[feature=19: SmartEnergy]
    end

    %% Mapping
    T1 --> E3
    T1 --> E4
    T1 --> E5
    T1 --> E6

    E3 --> F11
    E3 --> F19
    E4 --> F11
    E4 --> F18
    E5 --> F11
    E5 --> F18
    E6 --> F11
