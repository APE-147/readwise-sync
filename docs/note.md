我可以增加的部分, 与 cookie update 项目相结合
- 使用我自己的cookie
  - 需要更加快捷的调用方式
    - 命令行 -> 传入网址 -> 得到对应cookie








我需要这个项目提供一个功能

监控这个地址的文件。这个地址需要在项目根目录下的配置文件中进行定义。  
当该地址下有新增文件时，就解析新增的文件，参考 Readwise Reader 的 API 定义，将新增页面对应的内容通过 Readwise Reader 的 API 传送过去。  

Readwise Reader 的 API 相关定义参考这个页面 https://readwise.io/reader_api

需要的一些功能：  
1. 刚开始时，将该地址下的所有文件进行传送。(符合传输速率的限制)
2. 提供一个命令，运行后即可将新增的文件传送。 

