# WellnessFlow Flask App

一个简洁的 Flask full stack 项目，包含：

- 注册 / 登录
- SQLite 数据库
- 每日健康记录：
  - 喝水（ml）
  - 睡眠（时长 / 质量）
  - 运动（步数 / 时长）
- 番茄钟（专注 / 休息循环）
- 自动记录完成的专注次数和专注时长
- Calendar 月视图
- 每日 todo list
- 首页右侧显示今日 todo list

## 运行方式

```bash
pip install -r requirements.txt
python app.py
```

然后打开浏览器访问：

```bash
http://127.0.0.1:5000
```

## 项目结构

```text
wellness_tracker_app/
├── app.py
├── requirements.txt
├── README.md
├── instance/
└── app/
    ├── __init__.py
    ├── models.py
    ├── routes.py
    ├── static/
    │   └── style.css
    └── templates/
        ├── base.html
        ├── index.html
        ├── login.html
        ├── register.html
        ├── dashboard.html
        ├── logs.html
        └── calendar.html
```

## 说明

- 数据库文件会自动生成在 `instance/wellness.db`
- 当前版本是简洁实用版，适合继续扩展：
  - 图表统计
  - 用户头像
  - 提醒通知
  - 番茄钟历史分析
  - 更完整的 calendar event 系统
